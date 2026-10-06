/** Local check of the Word methodology: cd web && npx tsx scripts/methodology-docx-check.ts <out.docx> */
import { readFileSync, writeFileSync } from "node:fs";
import { methodologyDocx } from "../src/lib/methodology-docx";

methodologyDocx(readFileSync("../docs/methodology.md", "utf8")).then((buf) => {
  writeFileSync(process.argv[2], buf);
  console.log(`wrote ${process.argv[2]} (${buf.length} bytes)`);
});
