"""
Lifecycle manager for the standalone llama.cpp HTTP server (`llama-server.exe`).

Why this exists
---------------
On Windows the prebuilt CUDA wheels of ``llama-cpp-python`` from PyPI / abetlen
are stuck at version 0.3.4, whose bundled llama.cpp does not understand the
``qwen3`` GGUF architecture (added upstream in 2025-04). The maintained
standalone binaries from the official ``llama.cpp`` releases DO support qwen3
and ship CUDA 13 runtimes that match recent NVIDIA drivers.

So: instead of fighting Python bindings, we run the server binary as a
subprocess and talk to its OpenAI-compatible HTTP API. The chain code uses
``langchain_openai.ChatOpenAI`` pointed at ``http://127.0.0.1:<port>/v1``.

Process-lifetime guarantee (v0.7.8)
-----------------------------------
The child ``llama-server`` must NEVER outlive the backend, no matter how the
backend dies (clean exit, Ctrl-C, ``taskkill /F``, crash, OOM kill). Relying
on ``atexit`` alone is insufficient — it only runs on a *clean* interpreter
shutdown. We add an OS-level kill-on-parent-death binding:

  * Windows: a **Job Object** with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.
    The job handle is held by the backend process; when that process dies for
    ANY reason the OS closes its handles, and closing the last job handle
    terminates every process in the job — including llama-server.
  * Linux: ``prctl(PR_SET_PDEATHSIG, SIGKILL)`` in a ``preexec_fn`` so the
    kernel SIGKILLs the child the moment the parent thread dies.
  * macOS: no kernel primitive for this; we fall back to atexit + the
    explicit ``stop()`` on graceful shutdown.

The previous code used ``DETACHED_PROCESS`` specifically to *survive* parent
death — that was the root cause of the orphaned-process bug. We keep the
"no shared console" behaviour via ``CREATE_NO_WINDOW`` instead, which does
NOT detach the lifetime.
"""
from __future__ import annotations

import atexit
import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("llama_server")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Search order for the llama-server binary:
#   1. $LLAMA_SERVER_EXE              (explicit override)
#   2. ./runtime/llama-server.exe     (populated by setup.bat)
#   3. ./runtime/llama-server         (Linux / macOS, also populated by setup)
# The first existing path wins; if none exist, ServerConfig.start() raises a
# clear FileNotFoundError pointing the user at setup.bat.
def _resolve_default_server_exe() -> Path:
    env = os.environ.get("LLAMA_SERVER_EXE")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for candidate in (
        here / "runtime" / "llama-server.exe",
        here / "runtime" / "llama-server",
    ):
        if candidate.exists():
            return candidate
    # Fall through to the Windows default; .start() will surface a useful
    # error message if it doesn't exist yet.
    return here / "runtime" / "llama-server.exe"


DEFAULT_SERVER_EXE = _resolve_default_server_exe()
DEFAULT_MODEL_PATH = Path(
    os.environ.get("LLAMA_MODEL_PATH", "./models/model.gguf")
)


@dataclass
class ServerConfig:
    server_exe: Path = DEFAULT_SERVER_EXE
    model_path: Path = DEFAULT_MODEL_PATH
    host: str = "127.0.0.1"
    port: int = 8765
    # n_ctx is the TOTAL context across all slots; per-slot ctx = n_ctx / parallel.
    # 16384 / 4 = 4096 per slot, fine for typical queries and for ragas judges.
    n_ctx: int = 16384
    n_gpu_layers: int = -1   # -1 = all layers on GPU; 0 = CPU only
    # parallel >= 4 lets ragas's answer_relevancy fan out N completions in
    # a single request (it asks for paraphrased questions for cosine sim);
    # n=1 fails with 'n_cmpl > slots'. 4 is enough for the default ragas
    # configuration on a 4GB VRAM card.
    parallel: int = 4
    extra_args: list[str] = field(default_factory=list)
    startup_timeout_s: float = 90.0
    log_path: Path = Path("./llama_server.log")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def openai_base_url(self) -> str:
        return f"{self.base_url}/v1"


# ---------------------------------------------------------------------------
# Windows Job Object helpers (kill-on-parent-death)
# ---------------------------------------------------------------------------
# Implemented with ctypes so we don't depend on pywin32. A single job object
# is created per backend process; every llama-server we spawn is assigned to
# it. The job is configured to kill all its processes when the last handle to
# it closes — which happens automatically when the backend process dies.
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_CREATE_NO_WINDOW = 0x08000000


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _create_kill_on_close_job():
    """Create a Windows job object configured to terminate all member
    processes when its last handle closes. Returns the job handle, or None
    on any failure (caller falls back to atexit/stop())."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        h_job = kernel32.CreateJobObjectW(None, None)
        if not h_job:
            logger.warning("CreateJobObjectW failed (err=%d); orphan-guard "
                           "degraded to atexit only.", ctypes.get_last_error())
            return None

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        ok = kernel32.SetInformationJobObject(
            h_job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            logger.warning("SetInformationJobObject failed (err=%d).",
                           ctypes.get_last_error())
            kernel32.CloseHandle(h_job)
            return None
        return h_job
    except Exception:
        logger.exception("Job-object setup failed; orphan-guard degraded.")
        return None


def _assign_to_job(h_job, proc: subprocess.Popen) -> bool:
    """Assign ``proc`` to the job object. Returns True on success."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE,
        ]
        # subprocess.Popen._handle is the Windows process HANDLE (an int).
        ok = kernel32.AssignProcessToJobObject(h_job, int(proc._handle))
        if not ok:
            logger.warning("AssignProcessToJobObject failed (err=%d); "
                           "llama-server may orphan on hard kill.",
                           ctypes.get_last_error())
            return False
        return True
    except Exception:
        logger.exception("AssignProcessToJobObject raised.")
        return False


def _linux_pdeathsig() -> None:
    """preexec_fn for POSIX: ask the kernel to SIGKILL this child when its
    parent thread dies. Best-effort; Linux only (no-op elsewhere)."""
    try:
        import signal
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
    except Exception:
        # macOS / musl / anything without prctl: nothing we can do here.
        pass


# ---------------------------------------------------------------------------
# Lifecycle manager
# ---------------------------------------------------------------------------
class LlamaServer:
    """Spawn / health-check / shutdown a llama-server.exe process."""

    def __init__(self, config: Optional[ServerConfig] = None) -> None:
        self.config = config or ServerConfig()
        self._proc: Optional[subprocess.Popen] = None
        self._log_fh = None
        self._job = None  # Windows job-object handle (kill-on-close)

    # ------------------------------------------------------------------ probe
    def is_running(self) -> bool:
        try:
            r = requests.get(f"{self.config.base_url}/health", timeout=2)
            return r.status_code == 200
        except requests.RequestException:
            return False

    # -------------------------------------------------------------- start API
    def start(self) -> None:
        """Start the server if not already up. Idempotent."""
        if self.is_running():
            logger.info("Reusing already-running llama-server at %s",
                        self.config.base_url)
            return

        cfg = self.config
        if not cfg.server_exe.exists():
            raise FileNotFoundError(
                f"llama-server.exe not found at {cfg.server_exe}. "
                "Set LLAMA_SERVER_EXE env var to override."
            )
        if not cfg.model_path.exists():
            raise FileNotFoundError(f"GGUF model not found at {cfg.model_path}")

        cmd = [
            str(cfg.server_exe),
            "--model", str(cfg.model_path.resolve()),
            "--host", cfg.host,
            "--port", str(cfg.port),
            "--ctx-size", str(cfg.n_ctx),
            "--n-gpu-layers", str(cfg.n_gpu_layers),
            "--parallel", str(cfg.parallel),
            *cfg.extra_args,
        ]
        logger.info("Launching: %s", " ".join(cmd))

        cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(cfg.log_path, "w", encoding="utf-8", errors="replace")

        # v0.7.8: lifetime is now bound to the parent. On Windows we create a
        # kill-on-close job object BEFORE spawning and assign the child to it
        # immediately after; we use CREATE_NO_WINDOW (hide console) instead of
        # the old DETACHED_PROCESS (which deliberately survived parent death
        # and caused orphans). On POSIX we set PR_SET_PDEATHSIG via preexec.
        creationflags = 0
        preexec = None
        if os.name == "nt":
            creationflags = _CREATE_NO_WINDOW
            if self._job is None:
                self._job = _create_kill_on_close_job()
        else:
            preexec = _linux_pdeathsig

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=self._log_fh,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                preexec_fn=preexec,
            )
        except Exception as exc:
            logger.exception("Failed to spawn llama-server.")
            raise RuntimeError(f"Failed to spawn llama-server: {exc}") from exc

        # Bind the child's lifetime to ours (Windows job object).
        if os.name == "nt" and self._job is not None:
            if _assign_to_job(self._job, self._proc):
                logger.info("llama-server bound to kill-on-close job object.")

        # atexit remains as the graceful-shutdown path; the job object /
        # pdeathsig are the hard-kill safety net.
        atexit.register(self.stop)

        # Wait for /health to come up.
        deadline = time.time() + cfg.startup_timeout_s
        while time.time() < deadline:
            if self.is_running():
                elapsed = cfg.startup_timeout_s - (deadline - time.time())
                logger.info("llama-server ready after %.1fs at %s",
                            elapsed, cfg.base_url)
                return
            if self._proc.poll() is not None:
                tail = self._read_log_tail()
                raise RuntimeError(
                    "llama-server exited during startup. "
                    f"Last log output:\n{tail}"
                )
            time.sleep(0.5)

        # Timed out
        tail = self._read_log_tail()
        self.stop()
        raise TimeoutError(
            f"llama-server did not become ready within "
            f"{cfg.startup_timeout_s:.0f}s.\nLast log:\n{tail}"
        )

    # ----------------------------------------------------------- warmup API
    def warm_up(self, max_tokens: int = 1, timeout_s: float = 60.0) -> bool:
        """Fire a tiny completion so the model's weights + sampler + KV cache
        are hot before the user's first real question. ``start()`` already
        blocks until the model is loaded (``/health`` == 200), so this mainly
        primes the generation path and confirms end-to-end readiness.

        Returns True if the warmup completion succeeded. Never raises — a
        failed warmup is non-fatal (the first real query just pays the cost).
        """
        try:
            r = requests.post(
                f"{self.config.openai_base_url}/chat/completions",
                json={
                    "model": os.environ.get("HUSHDOC_MODEL_ID", "local-model"),
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "stream": False,
                },
                timeout=timeout_s,
            )
            ok = r.status_code == 200
            if ok:
                logger.info("llama-server warmup completion OK.")
            else:
                logger.warning("llama-server warmup returned HTTP %d.",
                               r.status_code)
            return ok
        except Exception as exc:
            logger.warning("llama-server warmup skipped (%s).", exc)
            return False

    # --------------------------------------------------------------- stop API
    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            logger.info("Stopping llama-server (pid=%d)", self._proc.pid)
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        if self._log_fh and not self._log_fh.closed:
            self._log_fh.close()
        self._log_fh = None
        # Closing the job handle would also kill any lingering members; do it
        # so a restart starts from a clean job.
        if os.name == "nt" and self._job is not None:
            try:
                ctypes.WinDLL("kernel32").CloseHandle(self._job)
            except Exception:
                pass
            self._job = None

    # ------------------------------------------------------------------ utils
    def _read_log_tail(self, n_chars: int = 1500) -> str:
        try:
            text = Path(self.config.log_path).read_text(
                encoding="utf-8", errors="replace"
            )
            return text[-n_chars:]
        except Exception:
            return "(no log available)"

    # --------------------------------------------------------- context helper
    def __enter__(self) -> "LlamaServer":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Singleton helper for the rest of the app
# ---------------------------------------------------------------------------
_SHARED: Optional[LlamaServer] = None


def get_shared_server(config: Optional[ServerConfig] = None) -> LlamaServer:
    """Return a process-wide singleton server, starting it on first call."""
    global _SHARED
    if _SHARED is None:
        _SHARED = LlamaServer(config)
        _SHARED.start()
    elif not _SHARED.is_running():
        _SHARED.start()
    return _SHARED
