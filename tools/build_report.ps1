<#
Build the PDF of the technical report from its Markdown, in English and in Chinese:
docs/technical_report.md -> build/technical_report.pdf, docs/technical_report_zh.md -> build/technical_report_zh.pdf.

pandoc writes a standalone LaTeX file (tools/report_pdf.lua drops the Markdown's own title block
and contents and turns links into the repository into text with their path; tools/report_pdf.tex
sets the code size and the page style), then XeLaTeX compiles it twice into build/ (the second
pass for the table of contents).  Needs pandoc 3 and XeLaTeX (MiKTeX or TeX Live) with the fonts
named below; on Windows they come with the system or with Microsoft Office.

    pwsh -NoProfile -File tools/build_report.ps1          # both languages
    pwsh -NoProfile -File tools/build_report.ps1 -Lang en
#>
param([ValidateSet('en', 'zh', 'both')][string]$Lang = 'both')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force build | Out-Null

$common = @(
    '--from=markdown-implicit_figures-tex_math_dollars-smart', '--standalone', '--shift-heading-level-by=-1', '--toc', '--toc-depth=2',
    '--lua-filter=tools/report_pdf.lua', '--include-in-header=tools/report_pdf.tex',
    '-V', 'documentclass=article', '-V', 'papersize=a4', '-V', 'geometry=margin=2cm',
    '-V', 'fontsize=10pt', '-V', 'colorlinks=true', '-V', 'linkcolor=blue', '-V', 'urlcolor=blue',
    '-V', 'monofont=DejaVu Sans Mono', '-M', 'date=2026-11-06',
    '-M', 'author=Haoyu Dong (h.dong@yale.edu)'
)
$docs = @{
    en = @{ src = 'docs/technical_report.md'; out = 'technical_report';
            args = @('-V', 'mainfont=Times New Roman', '-M', 'subtitle=Technical report and handoff, rf-clock 2.3.0.2') }
    zh = @{ src = 'docs/technical_report_zh.md'; out = 'technical_report_zh';
            # the usual Chinese set, as ctex's Windows fonts: Song for text, Hei for bold and headings,
            # Kai for italics, Fangsong for code; Times New Roman beside Song
            args = @('-V', 'mainfont=Times New Roman', '-V', 'CJKmainfont=SimSun', '-V', 'CJKsansfont=SimHei', '-V', 'CJKmonofont=FangSong',
                     '-V', 'CJKoptions=BoldFont=SimHei', '-V', 'CJKoptions=ItalicFont=KaiTi',
                     '-V', 'lang=zh-CN', '-M', 'subtitle=技术报告与交接文档，rf-clock 2.3.0.2') }
}
$which = if ($Lang -eq 'both') { @('en', 'zh') } else { @($Lang) }
foreach ($l in $which) {
    $d = $docs[$l]
    if (-not (Test-Path $d.src)) { Write-Warning "$($d.src) not found, skipped"; continue }
    $tex = "build/$($d.out).tex"
    pandoc $d.src @common @($d.args) -o $tex
    if ($LASTEXITCODE) { throw "pandoc failed for $($d.src)" }
    foreach ($pass in 1, 2) {
        xelatex -synctex=1 -interaction=nonstopmode -file-line-error -shell-escape -output-directory=build -aux-directory=build $tex | Out-Null
    }
    $log = "build/$($d.out).log"
    $missing = Select-String -Path $log -Pattern 'Missing character' | Measure-Object | Select-Object -ExpandProperty Count
    $over = Select-String -Path $log -Pattern 'Overfull \\hbox' | Measure-Object | Select-Object -ExpandProperty Count
    if (-not (Test-Path "build/$($d.out).pdf")) { throw "no PDF for $($d.src); see $log" }
    Write-Output "build/$($d.out).pdf  (missing characters: $missing, overfull boxes: $over)"
}
