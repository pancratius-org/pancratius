-- Pandoc filter for the PAN006B stanza oracle: drop the poem's unified author
-- sign-off so the oracle counts verse, not chrome.
--
-- The sign-off is normalized in the source DOCX to a standalone paragraph
-- `DD.MM.YYYY, <pen name>` (see the unify pass). That ASCII date prefix is all
-- this needs to match — no date/persona/locale heuristics, which is exactly why
-- normalizing the source first was worth it.
function Para(el)
  if pandoc.utils.stringify(el):match("^%s*%d%d%.%d%d%.%d%d%d%d,%s") then
    return {}
  end
end
