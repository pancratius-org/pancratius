-- Canonical dialogue labels are standalone bold paragraphs ending in a colon.
-- Keep the label with the start of its speech; the speech can still span pages.
function Para(el)
  if #el.content == 1 and el.content[1].t == 'Strong'
      and pandoc.utils.stringify(el.content[1]):match(':$') then
    local label = pandoc.write(pandoc.Pandoc({pandoc.Plain(el.content)}), 'typst')
    return pandoc.RawBlock('typst', '#block(sticky: true, spacing: auto)[' .. label .. ']')
  end
end
