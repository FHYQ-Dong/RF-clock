-- pandoc filter for the PDF of docs/technical_report.md (tools/build_report.ps1).
-- The Markdown opens with its title (H1), a subtitle and a byline, and lists its own contents
-- (H2 "Contents"); the PDF takes the title from the H1 (--shift-heading-level-by=-1, applied
-- after this filter), the subtitle, author and date from the command line, and makes its own
-- table of contents, so the paragraphs between the H1 and the first H2 and the Contents section
-- are dropped.  Links into the repository or results/ cannot be followed from a PDF: they become
-- their text with the path beside it.

-- In the Chinese translation the curly quotes are Chinese punctuation: pass them to xeCJK as they
-- are (full-width, in the Song face), instead of letting the LaTeX writer turn them into `` and ''
-- (Latin quotes from the Latin face, with a Latin-CJK gap on each side).
local chinese = (PANDOC_STATE.input_files[1] or ""):match("_zh%.md$") ~= nil

function Str(s)
  if not chinese then
    return nil
  end
  local out, i, text = pandoc.List(), 1, s.text
  while true do
    local a, b = text:find("\u{201C}", i, true)
    local c, d = text:find("\u{201D}", i, true)
    if c and (not a or c < a) then
      a, b = c, d
    end
    if not a then
      break
    end
    if a > i then
      out:insert(pandoc.Str(text:sub(i, a - 1)))
    end
    out:insert(pandoc.RawInline("latex", text:sub(a, b)))
    i = b + 1
  end
  if i == 1 then
    return nil -- no quotes
  end
  if i <= #text then
    out:insert(pandoc.Str(text:sub(i)))
  end
  return out
end

local function is_contents(header)
  local text = pandoc.utils.stringify(header)
  return text == "Contents" or text == "目录"
end

function Pandoc(doc)
  local out, before_first_h2, skipping = {}, true, false
  for _, block in ipairs(doc.blocks) do
    local keep = true
    if block.t == "Header" and block.level == 2 then
      before_first_h2 = false
      skipping = is_contents(block)
    elseif block.t == "Header" and block.level == 1 then
      keep = true -- the title
    elseif before_first_h2 then
      keep = false -- subtitle and byline
    end
    if skipping then
      keep = false
    end
    if keep then
      table.insert(out, block)
    end
  end
  doc.blocks = out
  return doc
end

function Link(link)
  local target = link.target
  if target:match("^%a+:") or target:match("^#") then
    return nil -- http(s), mailto, a section of this document: leave as a link
  end
  local path = target:gsub("^%.%./", ""):gsub("^%./", "")
  if not target:match("^%.%./") then
    path = "docs/" .. path -- a file beside this document
  end
  local text = pandoc.utils.stringify(link.content)
  if text == path or text == target then
    return pandoc.Code(path)
  end
  local out = pandoc.List(link.content)
  if text:find("[\228-\233]") then -- Chinese text (U+4E00-U+9FFF): full-width parentheses
    out:insert(pandoc.Str("（"))
    out:insert(pandoc.Code(path))
    out:insert(pandoc.Str("）"))
  else
    out:insert(pandoc.Str(" ("))
    out:insert(pandoc.Code(path))
    out:insert(pandoc.Str(")"))
  end
  return out
end
