// Un markdown léger (paragraphes, listes, gras, code, liens), construit en éléments du DOM :
// le texte du modèle n'est jamais interprété comme du HTML.
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/;

function inline(parent, text) {
  for (const part of text.split(INLINE)) {
    if (!part) continue;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      parent.append(
        Object.assign(document.createElement("strong"), {
          textContent: part.slice(2, -2),
        }),
      );
    } else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      parent.append(
        Object.assign(document.createElement("code"), {
          textContent: part.slice(1, -1),
        }),
      );
    } else if (part.startsWith("[") && part.endsWith(")") && part.includes("](")) {
      const [label, href] = part.slice(1, -1).split("](");
      const link = Object.assign(document.createElement("a"), {
        textContent: label,
        href,
      });
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      parent.append(link);
    } else {
      parent.append(part);
    }
  }
}

export function renderMarkdown(container, text) {
  container.replaceChildren();
  let list = null;
  for (const line of text.split("\n")) {
    const item = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (item) {
      if (!list) {
        list = document.createElement(/^\s*\d/.test(line) ? "ol" : "ul");
        container.append(list);
      }
      const li = document.createElement("li");
      inline(li, item[1]);
      list.append(li);
      continue;
    }
    list = null;
    if (!line.trim()) continue;
    const paragraph = document.createElement("p");
    inline(paragraph, line);
    container.append(paragraph);
  }
}
