import { FileText } from "lucide-react"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { SourceDoc } from "@/types"

/** Inline source-citation badges + collapsible details. */
export function Sources({
  docs,
  standaloneQuery,
}: {
  docs: SourceDoc[]
  standaloneQuery?: string
}) {
  if (!docs || docs.length === 0) return null

  // Group by filename + page so the inline badge row stays tidy.
  const seen = new Set<string>()
  const pills = docs
    .map((d) => ({ key: `${d.filename}#${d.page}`, doc: d }))
    .filter(({ key }) => (seen.has(key) ? false : (seen.add(key), true)))

  return (
    <div className="mt-2 space-y-2">
      <TooltipProvider delayDuration={200}>
        <div className="flex flex-wrap gap-1">
          {pills.map(({ key, doc }) => (
            <Tooltip key={key}>
              <TooltipTrigger asChild>
                <Badge variant="muted" className="cursor-help font-mono text-[10px]">
                  <FileText className="mr-1 h-2.5 w-2.5" />
                  {doc.filename} · p.{doc.page ?? "?"}
                </Badge>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm whitespace-pre-wrap text-left">
                {doc.headings && (
                  <div className="mb-1 font-medium">{doc.headings}</div>
                )}
                {doc.snippet}
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      </TooltipProvider>

      <Accordion type="single" collapsible className="border-none">
        <AccordionItem value="sources" className="border-none">
          <AccordionTrigger className="py-1 text-[11px] uppercase tracking-wide text-muted-foreground/80 hover:no-underline">
            Sources & retrieval debug
          </AccordionTrigger>
          <AccordionContent className="pb-2 pt-1">
            <div className="space-y-3 rounded-md border border-border/60 bg-muted/30 p-3 text-xs">
              {docs.map((d, i) => (
                <div key={i} className="space-y-0.5">
                  <div className="font-mono font-medium">
                    {d.filename}{" "}
                    <span className="text-muted-foreground">
                      · p.{d.page ?? "?"}
                      {d.headings ? ` · ${d.headings}` : ""}
                    </span>
                  </div>
                  <div className="text-muted-foreground">{d.snippet}</div>
                </div>
              ))}
              {standaloneQuery && (
                <div className="border-t pt-2 font-mono text-[10px] text-muted-foreground">
                  query: {standaloneQuery}
                </div>
              )}
            </div>
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  )
}
