import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { METHODOLOGY } from "@/content/methodology";
import PrintButton from "@/components/print-button";

export const metadata = { title: "Methodology · Komorebi Texas Screen" };

/** docs/methodology.md rendered (copied at build time by scripts/gen-methodology.mjs). */
export default function MethodologyPage() {
  return (
    <main className="mx-auto max-w-4xl px-4 py-6">
      <div className="flex justify-end gap-2 print:hidden">
        <a
          href="/methodology/docx"
          className="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100 dark:border-neutral-700 dark:hover:bg-neutral-900"
        >
          Download Word
        </a>
        <PrintButton />
      </div>
      <article
        className="text-sm leading-relaxed text-neutral-800 dark:text-neutral-200
          [&_h1]:mb-1 [&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:text-neutral-900 dark:[&_h1]:text-neutral-100
          [&_h2]:mt-7 [&_h2]:mb-2 [&_h2]:border-b [&_h2]:border-neutral-200 [&_h2]:pb-1 [&_h2]:text-lg [&_h2]:font-semibold dark:[&_h2]:border-neutral-800
          [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-6 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-6 [&_li]:my-1
          [&_code]:rounded [&_code]:bg-neutral-100 [&_code]:px-1 [&_code]:text-[0.85em] dark:[&_code]:bg-neutral-900
          [&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs
          [&_th]:border-b [&_th]:border-neutral-300 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold dark:[&_th]:border-neutral-700
          [&_td]:border-b [&_td]:border-neutral-100 [&_td]:px-2 [&_td]:py-1 [&_td]:align-top dark:[&_td]:border-neutral-900"
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{METHODOLOGY}</ReactMarkdown>
      </article>
    </main>
  );
}
