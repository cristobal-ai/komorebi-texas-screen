/**
 * docs/methodology.md → Word (the lender / JV-partner copy). Same markdown as the /methodology page, so the two
 * cannot drift. Handles what the note uses: headings, paragraphs, bold / italic / inline code, bullet and numbered
 * lists, GFM tables.
 */
import {
  AlignmentType,
  BorderStyle,
  Document,
  Footer,
  HeadingLevel,
  LevelFormat,
  Packer,
  PageNumber,
  Paragraph,
  ShadingType,
  Table,
  TableCell,
  TableRow,
  TextRun,
  WidthType,
} from "docx";
import type { Heading, List, PhrasingContent, Root, RootContent, Table as MdTable } from "mdast";
import remarkGfm from "remark-gfm";
import remarkParse from "remark-parse";
import { unified } from "unified";

const FONT = "Arial";
const PAGE_W = 12240; // US Letter, DXA
const MARGIN = 1440;
const CONTENT_W = PAGE_W - 2 * MARGIN;
const HEADINGS = [HeadingLevel.TITLE, HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3];

type Fmt = { bold?: boolean; italics?: boolean; code?: boolean };

function runs(nodes: PhrasingContent[], fmt: Fmt = {}, size?: number): TextRun[] {
  return nodes.flatMap((n): TextRun[] => {
    switch (n.type) {
      case "text":
        return [new TextRun({ text: n.value, bold: fmt.bold, italics: fmt.italics, font: fmt.code ? "Consolas" : FONT, size })];
      case "inlineCode":
        return [new TextRun({ text: n.value, font: "Consolas", bold: fmt.bold, size: size ? size - 1 : 19 })];
      case "strong":
        return runs(n.children, { ...fmt, bold: true }, size);
      case "emphasis":
        return runs(n.children, { ...fmt, italics: true }, size);
      case "link":
        return runs(n.children, fmt, size);
      case "break":
        return [new TextRun({ text: "", break: 1 })];
      default:
        return "value" in n && typeof n.value === "string" ? [new TextRun({ text: n.value, font: FONT, size })] : [];
    }
  });
}

const plain = (nodes: PhrasingContent[]): string =>
  nodes.map((n) => ("value" in n ? String(n.value) : "children" in n ? plain(n.children as PhrasingContent[]) : "")).join("");

function table(t: MdTable): Table {
  const rows = t.children;
  const ncol = Math.max(...rows.map((r) => r.children.length));
  // column widths in proportion to the average text length in each column (floored so short columns stay readable)
  const lens = Array.from({ length: ncol }, (_, c) =>
    Math.max(10, rows.reduce((s, r) => s + plain(r.children[c]?.children ?? []).length, 0) / rows.length));
  const total = lens.reduce((a, b) => a + b, 0);
  const widths = lens.map((l) => Math.floor((l / total) * CONTENT_W));
  widths[ncol - 1] += CONTENT_W - widths.reduce((a, b) => a + b, 0);
  const border = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map((r, ri) =>
      new TableRow({
        tableHeader: ri === 0,
        children: Array.from({ length: ncol }, (_, c) =>
          new TableCell({
            width: { size: widths[c], type: WidthType.DXA },
            shading: ri === 0 ? { type: ShadingType.CLEAR, color: "auto", fill: "E7E6E6" } : undefined,
            borders: { top: border, bottom: border, left: border, right: border },
            margins: { top: 40, bottom: 40, left: 80, right: 80 },
            children: [new Paragraph({ children: runs(r.children[c]?.children ?? [], { bold: ri === 0 }, 17) })],
          })),
      })),
  });
}

function blocks(nodes: RootContent[], state: { lists: number }): (Paragraph | Table)[] {
  return nodes.flatMap((n): (Paragraph | Table)[] => {
    switch (n.type) {
      case "heading": {
        const h = n as Heading;
        return [new Paragraph({ heading: HEADINGS[Math.min(h.depth - 1, 3)], children: runs(h.children) })];
      }
      case "paragraph":
        return [new Paragraph({ spacing: { after: 120 }, children: runs(n.children) })];
      case "list": {
        const l = n as List;
        const instance = ++state.lists;     // each numbered list restarts at 1
        return l.children.flatMap((item) =>
          item.children.flatMap((c) =>
            c.type === "paragraph"
              ? [new Paragraph({
                  numbering: { reference: l.ordered ? "numbers" : "bullets", level: 0, instance: l.ordered ? instance : undefined },
                  spacing: { after: 60 },
                  children: runs(c.children),
                })]
              : blocks([c], state)));
      }
      case "table":
        return [table(n as MdTable), new Paragraph({ spacing: { after: 120 }, children: [] })];
      case "thematicBreak":
        return [new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "BFBFBF", space: 1 } }, children: [] })];
      case "blockquote":
        return blocks(n.children, state);
      default:
        return [];
    }
  });
}

export function methodologyDocument(markdown: string): Document {
  const tree = unified().use(remarkParse).use(remarkGfm).parse(markdown) as Root;
  return new Document({
    creator: "Smart Investments",
    title: "Komorebi Texas Screen — Methodology",
    styles: {
      default: { document: { run: { font: FONT, size: 20 } } },
      paragraphStyles: [
        { id: "Title", name: "Title", basedOn: "Normal", run: { font: FONT, size: 36, bold: true }, paragraph: { spacing: { after: 80 } } },
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: FONT, size: 26, bold: true, color: "1F3864" }, paragraph: { spacing: { before: 280, after: 100 }, outlineLevel: 0 } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: FONT, size: 22, bold: true }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 1 } },
      ],
    },
    numbering: {
      config: [
        { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
        { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 360 } } } }] },
      ],
    },
    sections: [{
      properties: { page: { size: { width: PAGE_W, height: 15840 }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.RIGHT,
            children: [new TextRun({ text: "Komorebi Texas Screen — Methodology · page ", size: 16, color: "808080" }),
              new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "808080" })],
          })],
        }),
      },
      children: blocks(tree.children, { lists: 0 }),
    }],
  });
}

export async function methodologyDocx(markdown: string): Promise<Buffer> {
  return Packer.toBuffer(methodologyDocument(markdown));
}
