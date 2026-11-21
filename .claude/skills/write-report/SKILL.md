---
name: write-report
description: How to write, rewrite or review an experiment report, results README, design note, technical report, handoff or result slide in the RF-clock repo (results/*/REPORT.md and REPORT_zh.md, docs/, plan/, hw/*/DESIGN.md), and every figure that goes into one. Built from the user's corrections over many reports. Use it whenever an experiment has finished and needs writing up or analysing ("分析一下", "写报告", "写成 result", "出个图"), whenever a report or figure is revised, and whenever the user asks what a number, term, axis or figure in a report means. Such a question means the report failed, and the answer goes into the report as well as the chat.
---

# Writing reports the user can read

The reader is the user the next morning, or a lab member who never saw this conversation and knows nothing about RFSoCs or clock measurement. Every rule below comes from a report the user could not read and had to ask about. The user's own words are quoted so the reason behind each rule stays visible.

The project's standing rules are in `AGENTS.md`, section "Documents", with its list of terms (one name per concept). This skill does not repeat that list. Read it before writing, and use its names.

## 1. Start from the question, and answer only it

Before writing anything, write down in one sentence the question the experiment was run to answer, in the user's words. The report answers that question and nothing else.

- **The sections are the parts of the question.** When the user asked for the VCXO against the FS725, the report has two sections, the VCXO's deviation from the FS725 and the VCXO's temperature sensitivity. "你分两栏写，一个VCXO相对FS725的偏差，一个是VCXO的温度敏感性，不行吗？"
- **Leave out what does not answer it.** Data recorded the same night for another question (A's τ_ref) goes into the file list at most, or into a report of its own. A property of the reference (the frequency offset between two FS725s) stays out. If the reference must be shown good enough, one sentence says so with the number. "我让你测VCXO和FS725，你提另一个FS725干什么"
- **Do not analyse what was not measured.** If the thing's own temperature was not measured, there is no temperature coefficient, only a correlation with whatever was measured, named as a stand-in. Never analyse a quantity of a device whose input you do not have. "你分析FS725的温度敏感性干什么？你知道FS725的温度吗就在这分析？"
- **Conclusions first.** A short list at the top states the answer with its numbers and units, so that the reader knows what the rest supports. "所以结论是什么？"
- **Research, not marketing.** No large highlighted numbers, no selling. "我们是在搞研究，不是他妈的在搞营销"

## 2. A reader without this conversation must follow every line

"现在没有咱们对话记录根本看不懂你写的是什么"

- **Every number says what it is.** Say what was measured, between what and what, how it was computed, its unit, and which sign means what. A number the reader cannot place gets the question "17.61，40，-59.90是什么东西？？" or "4.6是什么？这是怎么算的？".
- **Every term is explained at its first use, in plain words.** Short explanations go in parentheses right after the term. This covers statistics (Allan deviation, what "overlapping" means), units (dBc, ppb), script options (`--acquire`), register or protocol fields (PPS_COUNT, slot), algorithms (Kalman, predictive steering) and vague words ("转换器" for which converter). Questions that came back were "重叠allan偏差的重叠是什么", "ppb是什么", "acquire是什么", "MAN_DAC 是什么".
- **Do not coin terms or use shorthand titles.** Describe the thing plainly instead. "Holdover error" became "the time error T seconds after the link stops", and the definition says how it was computed. "保持误差是什么" was the question that forced this. New terms that the project keeps are added to the list in `AGENTS.md`.
- **One notation per kind of quantity.** Do not mix ppm, ppb and 10⁻⁹ in one report. Give a fraction a physical meaning once ("1×10⁻⁹ means gaining or losing 1 ns each second"), or convert it to physical units (Hz of the 160 MHz VCXO, ps, ns per second).
- **Register and pin names only through a diagram.** Draw the hardware path as a block diagram and name a register only as the switch or block it controls ("HOLDOVER_FORCE, the switch that connects the VCXO's tuning voltage to the holdover DAC"). A data sheet figure is cited by document, page and title, and the part that matters is redrawn. When the manual has a usable figure of the internals, use it instead of a home-made ASCII drawing. "我不需要你写具体的哪个管脚叫什么名字，我需要知道这些名字对应的是图里面的那个分频器的哪段接线。另外，图13他妈的在哪？"
- **Anything the user had to ask goes into the report.** When the user asks about a report, answer in the chat and also rewrite the report so that the next reader does not need to ask. "你为什么不能在写报告的时候就这么写？非得让我问？" and "把我追问的这几个问题补在报告里面"
- **Events by clock time.** Give the date, the time and the time zone, with the time into the run in parentheses where it helps. "时间不写相对的，写绝对的"
- **Names.** Boards are named by S/N, taken from the run log. The remote machine is called RF-clock-PC in documents.
- **Know the audience.** For a technical report or a PI, leave out trivial programming details and version numbers and keep hardware detail to what the argument needs. "trivial的编程问题就别写进去了", "不要太暴露硬件细节"

## 3. Logic runs as a chain, in a fixed order

- **A mechanism is a complete chain.** Each link starts from the previous link's effect as its cause, and no step the reader needs is skipped. Say which links were measured and which are inferred. The user's own model of a chain:
  1. Different VCO bands need different tuning voltages to lock to 7.68 GHz.
  2. A different tuning voltage changes the charge pump's up and down currents, and the tuning node leaks, so a small time gap opens between the PLL's feedback edge and its reference edge, and that gap changes with the tuning voltage.
  3. The two LMX chips picked different bands because of noise, so their gaps differ, and every clock derived from them carries the difference.
  4. The difference shows in the capture codes, 5.3 ps per code, but MTS corrects in whole sample periods and cannot remove so small a step.
  5. It appears in the DAC to ADC loopback delay, and since each power-up or clock write picks bands anew, the loopback delay changes from one to the next.

  Listing facts side by side instead earned "你他妈依然不写完整逻辑".
- **A verified finding is written in one order.** First the phenomenon, then the background needed to follow it, then the causal chain, then the experiments that test the chain, and last their results.
- **Hardware is explained in the order the signal flows.** "能不能按照10MHz信号在板子上被分发的顺序写？然后每个模块给个图"
- **Say how the instrument limits the result, step by step.** When a curve's shape comes from the instrument, show how. For example, a counter read once a second to 10 ns adds 2.9 ns rms per reading, which gives an Allan deviation of 5×10⁻⁹/τ, and the measured points at 1 and 2 s lie on that line, so they show the counter and not the oscillator.
- **Comparisons define each number and say why errors cancel** when two measurements are subtracted.

## 4. Figures

Load the `dataviz` skill for chart mechanics. These are the rules the user has enforced.

- **One quantity per panel, one y axis.** Never twin y axes. Their scales are chosen arbitrarily, and whatever lines up between the two curves is then an artefact of that choice. "右下角为什么有两个轴？"
- **Never put unrelated series on one axis with a scale factor.** "VCXO / 1000" beside a raw FS725 lag was meaningless and was deleted. Each series gets its own panel with its own unit. "左下角的图什么意思？"
- **The title says in plain words what is plotted, against what.** "Holdover, rate from the same length before" could not be read. Write it as "Time error T seconds after the link stops". "右上角的图题什么意思？"
- **Every axis gives the quantity, the unit and the reference.** A bare ×10⁻⁹ has neither a unit nor a reference. Write "VCXO frequency less its mean (Hz), FS725 B as reference", or for a fraction say what it is a fraction of. "你图里的10^-9还是没有单位，不知道是相对于谁的"
- **The x axis is a meaningful variable.** Never use a run index or a reboot number of some board. Group the data by a variable that matters, for instance one figure with the DAC fixed and one with the ADC fixed. "没人想看你哪一次reboot的哪个板子"
- **Show the measurement's floor, and do not cut data silently.** Draw the instrument's resolution or noise floor on the plot. If points are left out, say why in the text. Cutting the Allan deviation at 10 s hid the dip and brought the question "为什么没有allan偏差先下去再上来？".
- **Figures are drawn from the data and agree with the text.** "你怎么着得出现一个26.04ps的测试数据吧？拿数据画图"
- **No clutter.** Leave out parameters that do not matter to the reader (I=16000), version numbers and process notes. Use text large enough to read, and put notes under each subplot, not beside it.
- **The text explains each figure panel by panel** (left, middle, right), saying what the reader should see in it.

## 5. Sentences and files

- **Complete sentences without colons or semicolons**, in both languages, except where the mark belongs to a notation (a time of day, a ratio). A list item starts with a bold label ending in a full stop, as in this file. Table cells under a header that names the subject may be noun or verb phrases.
- **Markdown is not hard-wrapped.** One line per paragraph or list item.
- **Links.** Every report mentioned is a relative link to its `REPORT.md`, or to `REPORT_zh.md` from a Chinese file. Data without a report stays as a code path such as `results/<folder>/<file>`.
- **English is the reference and `_zh.md` its translation.** Both change in the same edit.
- **The analysis script makes the figures.** The figures come from the experiment's `--analyze` (or its equivalent) so that they can be redrawn, and the report names the command.

## 6. When the measurement itself is indirect

If reaching the quantity takes several indirect steps (counting ticks, then subtracting a reference's own drift, then reading a proxy), say so in the report and propose the direct measurement. For the VCXO, the direct one is the board's own 1PPS made from the free-running clock against the FS725's 1PPS on the scope. "这个overnight实验不直观。直观的是不是直接..." Prefer the direct design when planning the next run.

## 7. Before handing the report over

Run the mechanical check, and fix everything it flags.

```
python .claude/skills/write-report/scripts/check_report.py <REPORT.md> <REPORT_zh.md>
```

Then read the report once as the reader without the conversation and answer each of these.

1. Does the first section give the question and the answer with numbers, and does every section serve that question?
2. For every number, does the text say what it is, between what, how it was computed, its unit and its sign?
3. Is every term explained where it first appears, and is it the name `AGENTS.md` uses?
4. Is anything analysed that was not measured, and is every stand-in named as one?
5. Does every mechanism run as a chain, with measured and inferred marked?
6. For every figure panel, is there one quantity and one y axis, a plain title, unit and reference on each axis, the instrument's floor where it matters, and a paragraph that explains it?
7. Has every question the user asked about this report or an earlier one been answered in the text?

When the user complains about one report, fix that report, then check the other reports for the same fault. "检查现有所有报告，统一按这个方式写"
