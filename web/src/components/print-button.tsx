"use client";

/** Opens the browser print dialog; "Save as PDF" there gives the dossier PDF (print styles in globals.css). */
export default function PrintButton() {
  return (
    <button
      onClick={() => window.print()}
      className="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100 print:hidden dark:border-neutral-700 dark:hover:bg-neutral-900"
    >
      Print / save as PDF
    </button>
  );
}
