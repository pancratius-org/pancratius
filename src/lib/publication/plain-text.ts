export function markdownToPlainText(markdown: string): string {
  let out = markdown;
  out = out.replace(/```[a-zA-Z0-9_-]*\n([\s\S]*?)```/g, "$1");
  out = out.replace(/^#{1,6}\s+/gm, "");
  out = out.replace(/!\[([^\]]*)]\([^)]+\)/g, "$1");
  out = out.replace(/\[([^\]]+)]\([^)]+\)/g, "$1");
  out = out.replace(/^>\s?/gm, "");
  out = out.replace(/^[\t ]*[-*+]\s+/gm, "");
  out = out.replace(/^[\t ]*\d+\.\s+/gm, "");
  out = out.replace(/(\*\*|__)(.*?)\1/g, "$2");
  out = out.replace(/(\*|_)(.*?)\1/g, "$2");
  out = out.replace(/~~(.*?)~~/g, "$1");
  out = out.replace(/`([^`]+)`/g, "$1");
  out = out.replace(/\\\n/g, "\n");
  out = out.replace(/[ \t]+\n/g, "\n");
  out = out.replace(/\n{3,}/g, "\n\n");
  return out.trim() + "\n";
}
