import { Fragment, type ReactNode } from "react";

// A deliberately small Markdown renderer for the analysis answers: ## headings,
// paragraphs, "-" / "1." lists, **bold**, *italic*, `code` and [links](url).
// The system prompt asks for exactly that subset. Everything becomes React
// elements — no HTML from the model is ever injected into the page — and it
// renders a half-streamed answer without flicker (an unclosed ** just shows).

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*([^*]+)\*\*|\*([^*\s][^*]*)\*|`([^`]+)`|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = `${keyPrefix}-${i++}`;
    if (m[1] !== undefined) out.push(<strong key={key} className="font-semibold text-primary">{m[1]}</strong>);
    else if (m[2] !== undefined) out.push(<em key={key}>{m[2]}</em>);
    else if (m[3] !== undefined)
      out.push(
        <code key={key} className="rounded bg-elevated px-1 py-0.5 text-[0.92em]">
          {m[3]}
        </code>,
      );
    else
      out.push(
        <a key={key} href={m[5]} target="_blank" rel="noreferrer noopener" className="text-accent underline underline-offset-2">
          {m[4]}
        </a>,
      );
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "p"; text: string }
  | { kind: "ul" | "ol"; items: string[] };

function parse(source: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) blocks.push({ kind: "p", text: para.join(" ") });
    para = [];
  };
  for (const raw of source.split("\n")) {
    const line = raw.trimEnd();
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      flush();
    } else if (heading) {
      flush();
      blocks.push({ kind: "h", level: heading[1].length, text: heading[2] });
    } else if (bullet || numbered) {
      flush();
      const kind = bullet ? "ul" : "ol";
      const text = (bullet ?? numbered)![1];
      const prev = blocks[blocks.length - 1];
      if (prev && prev.kind === kind) prev.items.push(text);
      else blocks.push({ kind, items: [text] });
    } else if (/^\s{2,}\S/.test(raw) && blocks.length && para.length === 0) {
      // Continuation line of a list item.
      const prev = blocks[blocks.length - 1];
      if (prev.kind === "ul" || prev.kind === "ol") prev.items[prev.items.length - 1] += ` ${line.trim()}`;
      else para.push(line.trim());
    } else {
      para.push(line.trim());
    }
  }
  flush();
  return blocks;
}

export default function Markdown({ source }: { source: string }) {
  return (
    <div className="flex flex-col gap-3 text-[14px] leading-relaxed text-secondary">
      {parse(source).map((b, i) => {
        const key = `b${i}`;
        switch (b.kind) {
          case "h":
            return b.level <= 2 ? (
              <h2 key={key} className="mt-3 font-display text-[17px] font-semibold tracking-tight text-primary first:mt-0">
                {inline(b.text, key)}
              </h2>
            ) : (
              <h3 key={key} className="mt-2 text-[14.5px] font-semibold text-primary first:mt-0">
                {inline(b.text, key)}
              </h3>
            );
          case "p":
            return <p key={key}>{inline(b.text, key)}</p>;
          case "ul":
          case "ol": {
            const List = b.kind === "ul" ? "ul" : "ol";
            return (
              <List key={key} className={`flex flex-col gap-1 pl-5 ${b.kind === "ul" ? "list-disc" : "list-decimal"}`}>
                {b.items.map((item, j) => (
                  <li key={j}>
                    <Fragment>{inline(item, `${key}-${j}`)}</Fragment>
                  </li>
                ))}
              </List>
            );
          }
        }
      })}
    </div>
  );
}
