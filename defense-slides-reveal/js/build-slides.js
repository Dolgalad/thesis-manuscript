// build-slides.js
const fs = require("fs");
const path = require("path");

const template = fs.readFileSync("index.template.html", "utf8");

const slideDir = "slides";

const slideFiles = fs
  .readdirSync(slideDir)
  .filter(file => file.endsWith(".html"))
  .sort();

const slides = slideFiles
  .map(file => fs.readFileSync(path.join(slideDir, file), "utf8"))
  .join("\n\n");

const output = template.replace("{{SLIDES}}", slides);

fs.writeFileSync("index.html", output);

console.log(`Built index.html from ${slideFiles.length} slide files.`);
