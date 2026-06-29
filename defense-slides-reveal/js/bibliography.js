import BibtexParser from "bibtex-js-parser";

async function buildBibliography() {
  const bibliography = document.getElementById("bibliography");

  if (!bibliography) {
    console.error("No element with id='bibliography' found.");
    return;
  }

  try {
    // Change this to "references.bib" if the file is next to index.html
    const bibPath = `${import.meta.env.BASE_URL}assets/defense-references.bib`;

    //const response = await fetch("assets/defense-references.bib");
    const response = await fetch(bibPath);


    if (!response.ok) {
      throw new Error(`Could not load assets/defense-references.bib: ${response.status} ${response.statusText}`);
    }

    const bibtex = await response.text();

    //if (typeof BibtexParser === "undefined") {
    //  throw new Error("BibtexParser is not defined. The parser script probably did not load.");
    //}

    const parsed = BibtexParser.parseToJSON(bibtex);

    console.log("Parsed BibTeX entries:", parsed);
    console.log("First parsed entry: ", parsed[0]);

    const entriesByKey = {};

    parsed.forEach(entry => {
      const key =
        entry.citationKey ||
        entry.key ||
        entry.id ||
        entry.entryKey;

      const tags =
        entry.entryTags ||
        entry.tags ||
        entry.fields ||
        entry;

      if (key) {
        entriesByKey[key] = tags;
      }
    });

    console.log("Available BibTeX keys:", Object.keys(entriesByKey));

    const citationElements = document.querySelectorAll("[data-cite]");
    const citedKeys = [];

    citationElements.forEach(el => {
      const keys = el.dataset.cite.trim().split(/\s+/);

      keys.forEach(key => {
        if (!citedKeys.includes(key)) {
          citedKeys.push(key);
        }
      });
    });

    console.log("Cited keys:", citedKeys);

    citationElements.forEach(el => {
      const keys = el.dataset.cite.trim().split(/\s+/);
    
      const links = keys.map(key => {
        const entry = entriesByKey[key];
        const label = entry ? formatCitationLabel(entry) : key;
    
        return `<a href="#/bibliography-section" class="citation-link">[${label}]</a>`;
      });
    
      el.innerHTML = links.join(" ");
      el.classList.add("citation");
    });

    const list = document.createElement("ol");
    list.className = "references";

    citedKeys.forEach(key => {
      const entry = entriesByKey[key];
      const item = document.createElement("li");
      item.id = `ref-${key}`;

      if (!entry) {
        item.innerHTML = `<strong>Missing BibTeX entry:</strong> ${key}`;
      } else {
        item.innerHTML = formatBibEntry(entry);
      }

      list.appendChild(item);
    });

    bibliography.innerHTML = "";
    bibliography.appendChild(list);

  } catch (error) {
    console.error(error);
    bibliography.innerHTML = `
      <p style="color: darkred; font-size: 0.7em;">
        Bibliography error: ${error.message}
      </p>
    `;
  }
}

function formatCitationLabel(entry) {
  const peopleField =
    entry.author ||
    entry.editor ||
    entry.bookauthor;

  const year = entry.year || "n.d.";
  const lastName = getFirstLastName(peopleField);

  if (!lastName) {
    return year;
  }

  const count = countPeople(peopleField);

  if (count > 1) {
    return `${lastName} et al., ${year}`;
  }

  return `${lastName}, ${year}`;
}

function formatBibEntry(entry) {
  const peopleField =
    entry.author ||
    entry.editor ||
    entry.bookauthor;

  const people = formatAuthors(peopleField);
  const year = entry.year || "n.d.";
  const title = cleanBibTeX(entry.title || "Untitled");

  const venue =
    entry.journal ||
    entry.booktitle ||
    entry.publisher ||
    "";

  const doi = entry.doi ? cleanBibTeX(entry.doi) : "";
  const url = entry.url ? cleanBibTeX(entry.url) : "";
  const eprint = entry.eprint ? cleanBibTeX(entry.eprint) : "";

  const link =
    doi ? `https://doi.org/${doi}` :
    url ? url :
    eprint ? `https://arxiv.org/abs/${eprint}` :
    "";

  let text = "";

  if (people) {
    text += `${people}. `;
  }

  if (link) {
    text += `<a href="${link}" target="_blank" rel="noopener noreferrer"><em>${title}</em></a>. `;
  } else {
    text += `<em>${title}</em>. `;
  }

  if (venue) {
    text += `${cleanBibTeX(venue)}, `;
  }

  text += `${year}.`;

  return text;
}

function getFirstLastName(authorField) {
  if (!authorField) return "";

  if (typeof authorField === "string") {
    const firstAuthor = authorField.split(/\s+and\s+/)[0].trim();
    const cleaned = cleanBibTeX(firstAuthor);

    if (cleaned.includes(",")) {
      return cleaned.split(",")[0].trim();
    }

    const parts = cleaned.split(/\s+/);
    return parts[parts.length - 1];
  }

  if (Array.isArray(authorField) && authorField.length > 0) {
    const first = authorField[0];

    if (typeof first === "string") {
      return getFirstLastName(first);
    }

    if (typeof first === "object") {
      return cleanBibTeX(
        first.last ||
        first.family ||
        first.lastName ||
        ""
      );
    }
  }

  if (typeof authorField === "object") {
    return cleanBibTeX(
      authorField.last ||
      authorField.family ||
      authorField.lastName ||
      ""
    );
  }

  return "";
}

function countPeople(authorField) {
  if (!authorField) return 0;

  if (typeof authorField === "string") {
    return authorField.split(/\s+and\s+/).length;
  }

  if (Array.isArray(authorField)) {
    return authorField.length;
  }

  if (typeof authorField === "object") {
    return 1;
  }

  return 0;
}

function formatAuthors(authorField) {
  if (!authorField) return "";

  // Case 1: normal BibTeX string:
  // "Nowozin, Sebastian and Lampert, Christoph H."
  if (typeof authorField === "string") {
    const authors = authorField.split(/\s+and\s+/);

    if (authors.length === 1) {
      return formatSingleAuthor(authors[0]);
    }

    if (authors.length === 2) {
      return `${formatSingleAuthor(authors[0])} and ${formatSingleAuthor(authors[1])}`;
    }

    return `${formatSingleAuthor(authors[0])} et al.`;
  }

  // Case 2: parser returns an array of author objects/strings
  if (Array.isArray(authorField)) {
    const authors = authorField.map(author => {
      if (typeof author === "string") {
        return formatSingleAuthor(author);
      }

      if (typeof author === "object") {
        const first =
          author.first ||
          author.given ||
          author.firstName ||
          "";

        const last =
          author.last ||
          author.family ||
          author.lastName ||
          "";

        const name = `${first} ${last}`.trim();

        if (name) return cleanBibTeX(name);

        return cleanBibTeX(JSON.stringify(author));
      }

      return "";
    }).filter(Boolean);

    if (authors.length === 1) {
      return authors[0];
    }

    if (authors.length === 2) {
      return `${authors[0]} and ${authors[1]}`;
    }

    if (authors.length > 2) {
      return `${authors[0]} et al.`;
    }

    return "";
  }

  // Case 3: parser returns a single author object
  if (typeof authorField === "object") {
    const first =
      authorField.first ||
      authorField.given ||
      authorField.firstName ||
      "";

    const last =
      authorField.last ||
      authorField.family ||
      authorField.lastName ||
      "";

    const name = `${first} ${last}`.trim();

    if (name) return cleanBibTeX(name);

    return "";
  }

  return "";
}
function formatSingleAuthor(author) {
  author = cleanBibTeX(author.trim());

  if (author.includes(",")) {
    const [last, first] = author.split(",").map(s => s.trim());
    return `${first} ${last}`;
  }

  return author;
}

function cleanBibTeX(text) {
  if (text === undefined || text === null) return "";

  return String(text)
    .replace(/[{}]/g, "")
    .replace(/\\&/g, "&")
    .replace(/\\_/g, "_")
    .replace(/--/g, "–")
    .trim();
}

buildBibliography();
