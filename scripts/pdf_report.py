"""
pdf_report.py —— 把各策略的結構化結果排成 A4 的 PDF 報表(版面照使用者給的範例)

流程:各策略結果(res[key]["meta"]["rows"])→ HTML → 無頭 Chrome/Edge 轉成 PDF。
字型:標題用 Noto Serif TC,內文用 Noto Sans TC(GitHub Actions 上要先裝 fonts-noto-cjk,
workflow 裡有);Windows 本機找不到時會退回微軟正黑體/新細明體,版面差不多。

版面:第1頁 標題 + 交集榜;第2頁 策略一、二;第3頁 策略三、四、五、七;第4頁 策略八、九;
第5頁 策略六(+ 週五的成效追蹤)。顏色用台股習慣:紅 = 漲/買超,綠 = 跌/賣超。
"""

import html as _html
import os
import re
from datetime import date
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

esc = lambda s: _html.escape(str(s), quote=True)  # noqa: E731

CSS = """
@page { size: A4; margin: 13mm 11mm 15mm;
  @bottom-left { content: "台股盤後掃描 __DAY__"; font: 7pt 'Noto Sans CJK TC','Noto Sans TC','Microsoft JhengHei',sans-serif; color: #8a93a3; }
  @bottom-right { content: counter(page) " / " counter(pages); font: 7pt 'Noto Sans CJK TC','Noto Sans TC','Microsoft JhengHei',sans-serif; color: #8a93a3; } }
* { box-sizing: border-box; }
body { margin: 0; font-family: 'Noto Sans CJK TC','Noto Sans TC','Microsoft JhengHei','PingFang TC',sans-serif;
  font-size: 8.4pt; color: #1b2330; line-height: 1.45; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
h1, h2, h3 { font-family: 'Noto Serif CJK TC','Noto Serif TC','PMingLiU','Songti TC',serif; margin: 0; color: #111826; }
h1 { font-size: 24pt; letter-spacing: .5px; margin: 2px 0 8px; }
h2 { font-size: 13.5pt; margin: 0 0 1px; }
h2 small { font: 400 8.5pt 'Noto Sans CJK TC','Noto Sans TC','Microsoft JhengHei',sans-serif; color: #7d8696; margin-left: 6px; }
.kicker { font: 8pt ui-monospace,'DejaVu Sans Mono',Consolas,monospace; color: #6b7585; letter-spacing: .5px; }
.lead { color: #3d4757; font-size: 9pt; max-width: 150mm; margin: 0 0 8px; }
.sub { color: #6b7585; font-size: 8pt; margin: 0 0 6px; }
hr { border: 0; border-top: 1px solid #dfe3ea; margin: 8px 0 12px; }
.page { break-before: page; }
.sec { margin: 0 0 15px; break-inside: avoid-page; }
.grid { display: grid; gap: 7px; align-items: start; }
.g2 { grid-template-columns: 1fr 1fr; } .g3 { grid-template-columns: 1fr 1fr 1fr; }
.card { border: 1px solid #dde2ea; border-radius: 6px; overflow: hidden; background: #fff; break-inside: avoid; }
.card > .ct { font-weight: 700; font-size: 8.8pt; padding: 5px 7px; border-bottom: 1px solid #e7ebf1; background: #fff; }
table { width: 100%; border-collapse: collapse; }
th { font-weight: 500; color: #6b7585; font-size: 7.2pt; text-align: right; padding: 3px 5px; border-bottom: 1px solid #e7ebf1; white-space: nowrap; }
th.l, td.l { text-align: left; }
td { padding: 2.6px 5px; text-align: right; border-bottom: 1px solid #eef1f5; vertical-align: middle; }
tr:last-child td { border-bottom: 0; }
tbody tr:nth-child(even) td { background: #f5f7fb; }
tr.conf td { background: #fdf0d9 !important; }
td.n { color: #8a93a3; width: 13px; text-align: left; }
.nm { font-weight: 700; } .code { font: 7.4pt ui-monospace,'DejaVu Sans Mono',Consolas,monospace; color: #6b7585; margin-left: 2px; }
.loc { color: #6b7585; font-size: 7pt; }
.dot { font-size: 8pt; color: #111826; } th.c, td.c { text-align: center; }
.chip { display: inline-block; font-size: 6.6pt; line-height: 1.5; padding: 0 4px; border-radius: 3px; margin-left: 3px; white-space: nowrap; font-weight: 500; }
.chip.warn { background: #fdecd0; color: #a8570a; } .chip.purple { background: #ece6f7; color: #5a2db3; }
.chip.info { background: #dcecf4; color: #1d5a78; } .chip.sell { background: #dff3e8; color: #1c7a4d; }
.chip.short { background: #efe3e3; color: #8a2d2d; } .chip.buy { background: #fde3e1; color: #b3261e; }
.chip.hi { background: #fde3e1; color: #b3261e; } .chip.lo { background: #dff3e8; color: #1c7a4d; }
.chip.long { background: #e6ecf5; color: #2c4a7c; }
.up { color: #c2271d; } .dn { color: #1d8a55; }
.bar { display: inline-block; width: 34px; height: 3px; background: #e7ebf1; border-radius: 2px; vertical-align: middle; margin-left: 4px; }
.bar i { display: block; height: 3px; border-radius: 2px; }
.bar.red i { background: #c2271d; } .bar.green i { background: #1d8a55; } .bar.blue i { background: #1f5f8b; }
.msg { border: 1px solid #e3e7ee; border-radius: 6px; padding: 7px 10px; color: #3d4757; background: #fafbfd; }
.legend { color: #6b7585; font-size: 7.4pt; margin: 0 0 6px; } .legend span { margin-right: 10px; }
.news { border: 1px solid #dde2ea; border-radius: 6px; overflow: hidden; }
.item { display: grid; grid-template-columns: 62mm 1fr; gap: 10px; padding: 7px 9px; border-bottom: 1px solid #eef1f5; break-inside: avoid; }
.item:last-child { border-bottom: 0; } .item .t { font-weight: 700; margin-bottom: 3px; } .item .why { color: #2d3645; }
.tag { display: inline-block; font-size: 6.8pt; padding: 0 4px; border-radius: 3px; margin-right: 5px; font-weight: 500; }
.tag.bull { background: #dff3e8; color: #1c7a4d; } .tag.bear { background: #fde3e1; color: #b3261e; }
.sc { display: inline-block; border: 1px solid #d6dce6; border-radius: 3px; padding: 0 4px; margin: 1px 3px 1px 0; font-size: 7.4pt; background: #fff; }
.foot { color: #7d8696; font-size: 7.2pt; margin-top: 8px; }
pre.perf { white-space: pre-wrap; font: 7.6pt/1.5 'Noto Sans CJK TC','Noto Sans TC','Microsoft JhengHei',sans-serif; margin: 0; padding: 8px 10px; }
"""


# ---------- 小工具 ----------


def _split(rows, n):
    k = -(-len(rows) // n) if rows else 0
    return [rows[i * k:(i + 1) * k] for i in range(n)] if k else [[] for _ in range(n)]


def _stock(ctx, sid, extra=""):
    chips = ""
    if ctx.risk:
        for kind, text in ctx.risk.tag_list(sid):
            cls = {"注意": "warn", "處置": "purple"}.get(kind, "info")
            chips += f'<span class="chip {cls}">{esc(text)}</span>'
    return f'<span class="nm">{esc(ctx.name(sid))}</span> <span class="code">{esc(sid)}</span>{extra}{chips}'


def _amt(v, unit=1e8, signed=True, digits=2):
    cls = "up" if v > 0 else "dn" if v < 0 else ""
    txt = f"{v / unit:+,.{digits}f}" if signed else f"{v / unit:,.{digits}f}"
    return f'<span class="{cls}">{txt}</span>'


def _mc(r):
    """策略一/五、交集榜:〔融資未增〕chip"""
    return '<span class="chip lo">融資未增</span>' if r.get("margin_clean") else ""


def _streak_chip(r):
    n = r.get("streak", 1)
    return f'<span class="chip warn">連{n}日</span>' if n >= 2 else ""


def _bar(frac, color):
    return f'<span class="bar {color}"><i style="width:{max(0, min(1, frac)) * 100:.0f}%"></i></span>'


def _section(title, count, sub, body, extra_cls=""):
    cnt = f"<small>{count} 檔</small>" if count is not None else ""
    return f'<div class="sec {extra_cls}"><h2>{esc(title)}{cnt}</h2><p class="sub">{esc(sub)}</p>{body}</div>'


def _msg(text):
    return f'<div class="msg">{esc(text)}</div>'


def _rows_of(res, key):
    r = res.get(key)
    return r.get("meta", {}).get("rows", []) if r else []


def _line0(res, key):
    r = res.get(key)
    return r["lines"][0] if r and r["lines"] else "今日無資料"


# ---------- 各區塊 ----------

CATS = [("價格位置", "價格", "位置"), ("策略二", "二", "分點"), ("策略七", "七", "地緣"), ("法人", "法人", "買超"), ("質押", "質", "押")]


def _intersection(ctx, res):
    rows = _rows_of(res, "intersection")
    if not rows:
        return _section("交集榜", 0, "同時出現在兩類以上偏多策略", _msg("今天沒有同時出現在兩類以上偏多策略的股票"))

    def table(chunk, start):
        head = "".join(f'<th class="c">{a}<br>{b}</th>' for _, a, b in CATS)
        body = ""
        for i, r in enumerate(chunk, start):
            conf = bool(r["conflicts"])
            cells = ""
            for key, _, _ in CATS:
                on = key in r["cats"]
                mark = '<span class="dot">●</span>' if on else ""
                if on and key == "價格位置" and r.get("price_kind"):
                    mark = f'<span class="chip {"hi" if r["price_kind"] == "創高" else "info"}">{r["price_kind"]}</span>'
                if key == "策略二" and "策略二賣超榜" in r["conflicts"]:
                    mark = '<span class="chip sell">賣</span>' + mark
                cells += f'<td class="c">{mark}</td>'
            short = '<span class="chip short">空</span>' if any("策略三" in c for c in r["conflicts"]) else ""
            for cname, ctext in (("借券大增榜", "借券增"), ("散戶接刀榜", "接刀"), ("申報轉讓榜", "申報轉讓"), ("解質榜", "解質")):
                if cname in r["conflicts"]:
                    short += f'<span class="chip short">{ctext}</span>'
            short += _mc(r)
            if r.get("streak", 1) >= 2:
                short += f'<span class="chip warn">連{r["streak"]}日</span>'
            body += (f'<tr class="{"conf" if conf else ""}"><td class="n">{i}</td>'
                     f'<td class="l">{_stock(ctx, r["sid"], short)}</td>{cells}</tr>')
        return (f'<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th>{head}</tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    a, b = _split(rows, 2)
    legend = ('<p class="legend"><span>● 入選該策略</span><span><span class="chip sell">賣</span> 同時在策略二賣超榜'
              '(訊號衝突,整列以底色標示)</span><span><span class="chip short">空</span> 同時在策略三(漲停放空)</span>'
              '<span><span class="chip short">借券增</span><span class="chip short">接刀</span><span class="chip short">申報轉讓</span> 同時在其他空方榜</span></p>')
    body = legend + f'<div class="grid g2">{table(a, 1)}{table(b, len(a) + 1) if b else ""}</div>'
    return _section("交集榜", len(rows), "同時出現在兩類以上偏多策略", body)


def _s1(ctx, res):
    rows = _rows_of(res, "s1")
    sub = "市值>500億,再漲3%內就創60日新高(距離由近到遠)"
    if not rows:
        return _section("策略一 創新高策略", 0, sub, _msg(_line0(res, "s1")))
    mx = max(r["gap"] for r in rows) or 1

    def table(chunk, start):
        body = "".join(
            f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, r["sid"], _mc(r))}</td><td>{r["close"]:,.2f}</td>'
            f'<td>{r["gap"] * 100:.1f}%{_bar(1 - r["gap"] / mx * 0.9, "blue")}</td></tr>'
            for i, r in enumerate(chunk, start))
        return (f'<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>收盤</th><th>距60日高</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    cols, start, out = _split(rows, 3), 1, ""
    for c in cols:
        if c:
            out += table(c, start)
            start += len(c)
    skipped = res["s1"].get("meta", {}).get("skipped", 0)
    foot = f'<p class="foot">另有 {skipped} 檔市值>500億的股票因還原股價不足60日或缺今天資料而無法判斷。</p>' if skipped else ""
    return _section("策略一 創新高策略", len(rows), sub, f'<div class="grid g3">{out}</div>{foot}')


def _s2(ctx, res):
    def card(key, title, color, sign):
        rows = _rows_of(res, key)
        mx = max((abs(r["net"]) for r in rows), default=1) or 1
        body = "".join(
            f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, r["sid"], _streak_chip(r))}</td>'
            f'<td>{_amt(r["net"])}{_bar(abs(r["net"]) / mx, color)}</td><td>{r["n"]}</td></tr>'
            for i, r in enumerate(rows, 1)) or '<tr><td class="l" colspan="4">沒有符合的股票</td></tr>'
        return (f'<div class="card"><div class="ct">{title}</div><table><thead><tr><th class="l">#</th><th class="l">股票</th>'
                f'<th>合計(億)</th><th>分點數</th></tr></thead><tbody>{body}</tbody></table></div>')

    body = (f'<div class="grid g3">{card("s2_buy", "今日買超 Top10", "red", 1)}{card("s2_sell", "今日賣超 Top10", "green", -1)}'
            f'{card("s2_buy3", "近3日買超 Top10", "red", 1)}</div>')
    return _section("策略二 前40大分點", None, "今日與近3日合計買賣超(原始單位為萬元,這裡換算成億元;分點數 = 買超/賣超的分點組數)", body)


def _s3(ctx, res):
    if "s3" not in res:  # 策略三已停用
        return ""
    rows = _rows_of(res, "s3")
    sub = "漲停價成交量占當日>30%,市值>500億"
    if not rows:
        return _section("策略三 漲停放空", None, sub, _msg(_line0(res, "s3")))
    body = "".join(
        f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, r["sid"])}</td><td>{r["limit_price"]:,.2f}</td>'
        f'<td>{r["ratio"] * 100:.1f}%</td><td>{r["vol"] / 1000:,.0f} / {r["total"] / 1000:,.0f}</td><td class="l">{esc(r["short"])}</td></tr>'
        for i, r in enumerate(rows, 1))
    table = ('<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>漲停價</th><th>漲停價成交占比</th>'
             f'<th>漲停價量/總量(張)</th><th class="l">融券</th></tr></thead><tbody>{body}</tbody></table></div>')
    return _section("策略三 漲停放空", len(rows), sub, table)


def _s4(ctx, res):
    rows = _rows_of(res, "s4")
    sub = "市值>500億,依MoM由大到小"
    if not rows:
        return _section("策略四 當日營收公布", 0, sub, _msg(_line0(res, "s4")))
    month = rows[0]["month"]

    def table(chunk):
        body = ""
        for r in chunk:
            mom = "-" if r["mom"] is None else _amt(r["mom"], 1, True, 1) + "%"
            flag = ""
            if r["flag"]:
                flag = f'<span class="chip {"hi" if "高" in r["flag"] else "lo"}">{esc(r["flag"])}</span>'
            body += (f'<tr><td class="l">{_stock(ctx, r["sid"])}</td><td>{r["rev"] / 1e8:,.2f}</td><td>{mom}</td><td class="l">{flag}</td></tr>')
        return (f'<div class="card"><table><thead><tr><th class="l">股票</th><th>{month}月營收(億)</th><th>MoM</th><th class="l">記錄</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    a, b = _split(rows, 2)
    return _section("策略四 當日營收公布", len(rows), sub, f'<div class="grid g2">{table(a)}{table(b) if b else ""}</div>')


def _s5(ctx, res):
    rows = _rows_of(res, "s5")
    sub = "市值>500億,今日收盤創近60交易日新高"
    if not rows:
        return _section("策略五 創新高", 0, sub, _msg(_line0(res, "s5")))

    def table(chunk, start):
        body = ""
        for i, r in enumerate(chunk, start):
            chg = "-" if r["chg"] is None else _amt(r["chg"], 1, True, 1) + "%"
            body += f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, r["sid"], _mc(r))}</td><td>{r["close"]:,.2f}</td><td>{chg}</td></tr>'
        return (f'<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>收盤</th><th>漲跌</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    a, b = _split(rows, 2)
    return _section("策略五 創新高", len(rows), sub, f'<div class="grid g2">{table(a, 1)}{table(b, len(a) + 1) if b else ""}</div>')


def _s7(ctx, res):
    rows = _rows_of(res, "s7")
    sub = "分點單日或近5日淨買超>5,000萬(同區:市值>500億;庫藏股:不限市值)"
    if not rows:
        return _section("策略七 地緣分點買進", 0, sub, _msg("今天沒有符合條件的股票"))
    body = ""
    for r in rows:
        cls = "buy" if "庫藏股" in r["kind"] else "long"
        loc = f' <span class="loc">{esc(r["loc"])}</span>' if r["loc"] else ""
        body += (f'<tr><td class="l">{_stock(ctx, r["sid"], loc)}</td><td class="l"><span class="chip {cls}">{esc(r["kind"])}</span></td>'
                 f'<td>{_amt(r["nd"], 1e4, True, 0)}</td><td>{_amt(r["n5"], 1e4, True, 0)}</td><td class="l">{esc("、".join(r["tops"]) or "-")}</td></tr>')
    table = ('<div class="card"><table><thead><tr><th class="l">股票</th><th class="l">類型</th><th>單日(萬)</th><th>近5日(萬)</th>'
             f'<th class="l">主要買超分點</th></tr></thead><tbody>{body}</tbody></table></div>')
    return _section("策略七 地緣分點買進", len(rows), sub, table)


def _s8(ctx, res):
    def card(key, title):
        rows = _rows_of(res, key)
        mx = max((r["total"] for r in rows), default=1) or 1
        body = "".join(
            f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, r["sid"])}</td><td>{_amt(r["total"])}{_bar(r["total"] / mx, "red")}</td>'
            f'<td>{_amt(r["foreign"])}</td><td>{_amt(r["trust"])}</td></tr>' for i, r in enumerate(rows, 1)) \
            or '<tr><td class="l" colspan="5">沒有符合的股票</td></tr>'
        return (f'<div class="card"><div class="ct">{title}</div><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>合計(億)</th>'
                f'<th>外資</th><th>投信</th></tr></thead><tbody>{body}</tbody></table></div>')

    body = f'<div class="grid g2">{card("s8_1", "單日買超 Top10")}{card("s8_5", "近5日買超 Top10")}</div>'
    return _section("策略八 法人買超排行", None, "外資+投信合計,市值>500億,單位億元", body)


def _s9(ctx, res):
    def card(key, title):
        rows = _rows_of(res, key)
        mx = max((r["streak"] for r in rows), default=1) or 1
        body = ""
        for i, r in enumerate(rows, 1):
            ratio = "-" if r["ratio"] is None else "%.1f%%" % (r["ratio"] * 100)
            plus = "+" if r["capped"] else ""
            body += (f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, r["sid"])}</td>'
                     f'<td>{r["streak"]}{plus} 天{_bar(r["streak"] / mx, "blue")}</td><td>{_amt(r["amt"])}</td><td>{ratio}</td></tr>')
        body = body or '<tr><td class="l" colspan="5">沒有符合的股票</td></tr>'
        return (f'<div class="card"><div class="ct">{title}</div><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>連買</th>'
                f'<th>合計(億)</th><th>占成交</th></tr></thead><tbody>{body}</tbody></table></div>')

    body = f'<div class="grid g2">{card("s9_trust", "投信")}{card("s9_foreign", "外資")}</div>'
    return _section("策略九 法人連續買超", None, "連買3天以上且合計>1億(市值>500億),單位億元", body)


def _s10(ctx, res):
    r = res.get("s10")
    sub = "用族群 ETF 代表:美國取前一個已收盤交易日、日本取今天收盤;還原收盤價創近60交易日新高"
    if not r:
        return ""
    rows = r.get("meta", {}).get("rows", [])
    failed = r.get("meta", {}).get("failed", [])
    if not rows:
        return _section("策略十 美日族群創新高", None, sub, _msg(" / ".join(r["lines"][:2])))

    def card(mkt):
        sel = [x for x in rows if x["market"] == mkt]
        day_used = r.get("meta", {}).get("dates", {}).get(mkt) or (sel[0]["date"] if sel else "-")
        body = ""
        for i, x in enumerate(sel, 1):
            star = '<span class="chip warn">52週新高</span>' if x["new52w"] else ""
            r5 = "-" if x["ret5"] is None else _amt(x["ret5"] * 100, 1, True, 1) + "%"
            body += (f'<tr><td class="n">{i}</td><td class="l"><b>{esc(x["name"])}</b> '
                     f'<span class="code">{esc(x["symbol"].replace(".T", ""))}</span> {star}</td>'
                     f'<td>{_amt(x["chg"] * 100, 1, True, 1)}%</td><td>{r5}</td></tr>')
        if not sel:
            body = '<tr><td class="l" colspan="4">今天沒有族群創60日新高</td></tr>'
        return (f'<div class="card"><div class="ct">{mkt}({esc(day_used)} 收盤)</div><table><thead><tr><th class="l">#</th>'
                f'<th class="l">族群</th><th>單日</th><th>近5日</th></tr></thead><tbody>{body}</tbody></table></div>')

    foot = f'<p class="foot">沒抓到資料或資料不足:{esc("、".join(failed))}</p>' if failed else ""
    body = f'<div class="grid g2">{card("美國")}{card("日本")}</div>{foot}'
    return _section("策略十 美日族群創新高", len(rows), sub, body)



def _group(ctx, res):
    r = res.get("group")
    if not r:
        return ""
    rows = r.get("meta", {}).get("rows", [])
    sub = "同一族群當天 ≥3 檔出現在偏多榜(自訂族群 data/groups.json;預設產業別要 ≥5 檔且 ≥20%);族群內有空方訊號會標出"
    if not rows:
        return _section("族群訊號", 0, sub, _msg(r["lines"][0] if r["lines"] else "今天沒有族群共振"))
    cards = ""
    for g in rows:
        run = f'<span class="chip warn">連{g["streak"]}日</span>' if g.get("streak", 1) >= 2 else ""
        chips = "".join(
            f'<span class="sc">{esc(ctx.name(m["sid"]))} <span class="code">{esc(m["sid"])}</span> '
            f'<span class="loc">{esc("+".join(m["tags"]))}</span></span>' for m in g["members"])
        bear = ""
        if g["bears"]:
            bear = ('<div class="loc" style="color:#b3261e;margin-top:3px">⚠族群內有空方訊號:'
                    + "、".join(f'{esc(ctx.name(b["sid"]) or b["sid"])}〔{esc(b["label"])}〕' for b in g["bears"]) + "</div>")
        cards += (f'<div class="card" style="padding:6px 9px"><div style="font-weight:700;margin-bottom:3px">{esc(g["group"])} '
                  f'<span class="loc">{len(g["members"])} 檔</span>{run}</div><div>{chips}</div>{bear}</div>')
    return _section("族群訊號", len(rows), sub, f'<div class="grid g2">{cards}</div>')


def _e_knife(ctx, res):
    r = res.get("e_knife")
    sub = "市值>500億,近5日融資餘額增加≥10% 但同期股價漲幅<2%(偏空;進交集榜訊號衝突;成效追蹤反向計算)"
    if not r:
        return ""
    rows = r.get("meta", {}).get("rows", [])
    if not rows:
        return _section("散戶接刀榜", 0, sub, _msg(r["lines"][0] if r["lines"] else "今天沒有符合條件的股票"))

    def table(chunk, start):
        body = "".join(
            f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, x["sid"])}</td>'
            f'<td>{_amt(x["margin_pct"] * 100, 1, True, 1)}%</td><td>{x["bal"]:,.0f}</td>'
            f'<td>{_amt(x["px"] * 100, 1, True, 1)}%</td></tr>' for i, x in enumerate(chunk, start))
        return ('<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>融資5日</th>'
                f'<th>融資餘額(張)</th><th>股價5日</th></tr></thead><tbody>{body}</tbody></table></div>')

    a, b = _split(rows, 2)
    return _section("散戶接刀榜", len(rows), sub, f'<div class="grid g2">{table(a, 1)}{table(b, len(a) + 1) if b else ""}</div>')


def _s13(ctx, res):
    r = res.get("s13")
    if not r:
        return ""
    sub = "市值>500億;任一成立:單日餘額增加≥流通股數0.5%〔a〕/ 連3日增加且合計≥0.3%〔b〕/ 單日借券淨增加(賣出−還券)× 收盤價 ≥1億元〔c〕(偏空)。單位:萬張、億元"
    up = r.get("meta", {}).get("rows", [])
    down = res.get("s13_cover", {}).get("meta", {}).get("rows", [])
    if not up and not down and r["lines"] and "今日無資料" in r["lines"][0]:
        return _section("策略十三 借券賣出餘額", None, sub, _msg(r["lines"][0]))

    def wz(v, signed=False):
        return f"{v / 1e7:+,.2f}" if signed else f"{v / 1e7:,.2f}"

    def card(title, rows, cover):
        body = "".join(
            f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, x["sid"])}</td><td>{wz(x["bal"])}</td>'
            f'<td>{x["ratio"] * 100:.2f}%</td><td>{_amt(x["day"] / 1e7, 1, True, 2)}</td>'
            + ("" if cover else f'<td>{_amt(x["three"] / 1e7, 1, True, 2)}</td><td>{x.get("sales", 0) / 1e7:,.2f}</td><td>{x.get("returns", 0) / 1e7:,.2f}</td><td>{x.get("amt", 0) / 1e8:+.2f}</td><td class="c">{esc(x["tag"])}</td>')
            + "</tr>" for i, x in enumerate(rows, 1)) or '<tr><td class="l" colspan="10">今天沒有符合條件的股票</td></tr>'
        extra = "" if cover else '<th>3日</th><th>賣出</th><th>還券</th><th>淨增加金額(億)</th><th class="c">條件</th>'
        return (f'<div class="card"><div class="ct">{title}</div><table><thead><tr><th class="l">#</th><th class="l">股票</th>'
                f'<th>餘額</th><th>占流通</th><th>單日</th>{extra}</tr></thead><tbody>{body}</tbody></table></div>')

    body = (f'<div class="grid g2">{card("借券餘額大增(偏空)", up, False)}'
            f'{card("借券餘額大減 Top5(回補,只顯示)", down, True)}</div>')
    return _section("策略十三 借券賣出餘額", len(up), sub, body)


def _s14(ctx, res):
    r = res.get("s14")
    if not r:
        return ""
    m = r.get("meta", {})
    sub = f"集保週資料 {m.get('data_date', '')}(只在新一週入庫日出現);大戶=1,000張以上,散戶=400張以下;市值>500億"
    up = m.get("rows", [])
    down = res.get("s14_down", {}).get("meta", {}).get("rows", [])
    if not up and not down and r["lines"] and "今日無資料" in r["lines"][0]:
        return _section("策略十四 大戶持股比", None, sub, _msg(r["lines"][0]))

    def card(title, rows):
        body = "".join(
            f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, x["sid"])}</td>'
            f'<td>{x["b2"]:.1f}→{x["b1"]:.1f}→{x["b0"]:.1f}</td><td>{_amt(x["b0"] - x["b2"], 1, True, 2)}</td>'
            f'<td>{_amt(x["r0"] - x["r2"], 1, True, 2)}</td></tr>' for i, x in enumerate(rows, 1)
        ) or '<tr><td class="l" colspan="5">本週沒有符合條件的股票</td></tr>'
        return (f'<div class="card"><div class="ct">{title}</div><table><thead><tr><th class="l">#</th><th class="l">股票</th>'
                f'<th>大戶%(2週前→上週→本週)</th><th>大戶Δpp</th><th>散戶Δpp</th></tr></thead><tbody>{body}</tbody></table></div>')

    return _section("策略十四 大戶持股比", len(up), sub,
                    f'<div class="grid g2">{card("上升(偏多)", up)}{card("下降 Top5(籌碼轉差,只顯示)", down)}</div>')


def _gm(vals):
    return "→".join("-" if v is None else f"{v * 100:.1f}" for v in vals) + "%"


def _s15(ctx, res):
    ev = res.get("s15")
    if not ev:
        return ""
    out = ""
    m = ev.get("meta", {})
    rows = m.get("rows", [])
    sub = "今天新公布的季報(市值>500億):毛利率連2季升且高於去年同季〔a〕/ 營業利益轉正〔b〕/ 毛利率單季+3pp〔c〕"
    if rows:
        body = "".join(
            f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, x["sid"])}</td><td>{esc(x["q"])}</td>'
            f'<td>{_gm(x["gms"])}</td><td class="c">{esc(x["tag"])}</td>'
            f'<td>{"-" if x["om"] is None else format(x["om"] * 100, ".1f") + "%"}</td></tr>' for i, x in enumerate(rows, 1))
        note = ""
        if m.get("total", 0) > len(rows):
            note += f"今天共 {m['total']} 檔符合,只列前 {len(rows)} 檔。 "
        if m.get("skipped"):
            note += f"略過 {m['skipped']} 檔(沒有毛利資料,例如金融股)。"
        tbl = ('<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>季別</th><th>毛利率 近4季</th>'
               f'<th class="c">條件</th><th>營益率</th></tr></thead><tbody>{body}</tbody></table></div>'
               + (f'<p class="foot">{esc(note)}</p>' if note else ""))
        out += _section("策略十五 財報毛利率轉折", len(rows), sub, tbl)
    elif ev["lines"] and "今日無資料" in ev["lines"][0]:
        out += _section("策略十五 財報毛利率轉折", None, sub, _msg(ev["lines"][0]))
    else:
        out += _section("策略十五 財報毛利率轉折", 0, sub, _msg("今天沒有符合條件的新季報" + (
            f"(略過 {m['skipped']} 檔沒有毛利資料)" if m.get("skipped") else "")))
    wk = res.get("s15_weak", {}).get("meta", {}).get("rows", [])
    if wk:
        body = "".join(f'<tr><td class="l">{_stock(ctx, x["sid"])}</td><td>{_gm(x["gms"])}</td></tr>' for x in wk)
        out += (f'<div class="sec"><div class="card"><div class="ct">〔毛利轉弱〕毛利率和營益率都連2季下降(只顯示)</div><table><thead><tr>'
                f'<th class="l">股票</th><th>毛利率</th></tr></thead><tbody>{body}</tbody></table></div></div>')

    def mom(key, title):
        r = res.get(key)
        if not r:
            return ""
        mm = r.get("meta", {})
        rws = mm.get("rows", [])
        if not rws:
            return f'<div class="sec"><div class="msg">{esc(title)}:本期沒有符合條件的股票</div></div>'
        body = ""
        for x in rws:
            flag = ""
            if x.get("label") == "新進":
                flag += '<span class="chip buy">新進</span>'
            elif x.get("streak", 1) >= 2:
                flag += f'<span class="chip warn">連{x["streak"]}期</span>'
            if x["yearly"]:
                flag += '<span class="chip info">年增</span>'
            if x["high"]:
                flag += '<span class="chip hi">高成長</span>'
            yoy = "-" if x["yoy"] is None else f'{x["yoy"] * 100:+.0f}%(3M {x["yoy3"] * 100:+.0f}%)'
            body += (f'<tr><td class="l">{_stock(ctx, x["sid"], flag)}</td><td>{yoy}</td>'
                     f'<td>{_gm(x["gms"])}</td><td>{_gm(x["oms"])}</td></tr>')
        ex = ""
        if mm.get("exited"):
            ex = f'<p class="foot">掉出榜:{esc("、".join((ctx.name(s) or s) + "(" + s + ")" for s in mm["exited"]))}</p>'
        return (f'<div class="sec"><div class="card"><div class="ct">{esc(title)} <span class="loc">{esc(mm.get("reason", ""))}</span></div>'
                f'<table><thead><tr><th class="l">股票</th><th>營收YoY</th><th>毛利率(Q-2→Q)</th><th>營益率(Q-2→Q)</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>{ex}</div>')

    if "s15_mom" in res and (res["s15_mom"].get("meta", {}).get("reason") or res["s15_mom"].get("meta", {}).get("rows")):
        out += _section("基本面動能榜", None,
                        "營收年增 + 毛利率 + 營益率連續季增(市值>500億);只在月營收公布後第一個交易日、或有新季報入庫的日子出現",
                        mom("s15_mom", "〔三升〕營收年增 + 毛利率 + 營益率連續季增") + mom("s15_mom2", "〔二升〕毛利率、營益率連續季增但營收年增沒過"))
    return out


def _s11(ctx, res):
    r = res.get("s11")
    if not r:
        return ""
    m = r.get("meta", {})
    sub = ("董監事/經理人/大股東當月設質股數變化(月頻:這個月 vs 上個月),|變化|≥10% 持股才列;"
           "〔設質〕偏多、〔解質〕偏空;月報看不出哪一天,也分不出借款到期還款等雜訊")
    up = m.get("rows", [])
    down = res.get("s11_release", {}).get("meta", {}).get("rows", [])
    if not up and not down and r["lines"] and "今日無資料" in r["lines"][0]:
        return _section("策略十一 質押/解質(月頻)", None, sub, _msg(r["lines"][0]))

    def card(title, rows):
        body = ""
        for i, x in enumerate(rows, 1):
            mv = "-" if not x.get("mv") else f'{x["mv"] / 1e8:,.0f}億'
            body += (f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, x["sid"])}</td><td>{mv}</td>'
                     f'<td class="l">{esc(x["title"])} {esc(x["name"])}</td><td>{x["delta"] / 1e4:+,.0f}萬股</td>'
                     f'<td>{x["ratio"] * 100:.0f}%</td><td>{x["total_ratio"] * 100:.0f}%</td></tr>')
        body = body or '<tr><td class="l" colspan="7">本月沒有符合條件的</td></tr>'
        return (f'<div class="card"><div class="ct">{title}</div><table><thead><tr><th class="l">#</th><th class="l">股票</th>'
                f'<th>市值</th><th class="l">申報人</th><th>設質變化</th><th>占持股</th><th>累計質押</th></tr></thead>'
                f'<tbody>{body}</tbody></table></div>')

    foot = f'<p class="foot">資料月份 {esc(m.get("ym", ""))} 對比 {esc(m.get("prev", ""))}</p>'
    return _section("策略十一 質押/解質(月頻)", len(up) + len(down), sub,
                    card("〔設質〕設質增加(偏多)", up) + card("〔解質〕設質減少(偏空)", down) + foot)


def _s16(ctx, res):
    r = res.get("s16")
    if not r:
        return ""
    sub = "董監事/經理人/大股東事前申報(今天出表的申報);門檻:占其持股≥10% 或 約≥1億元;一般/鉅額交易=偏空,信託/贈與只列出"
    rows = r.get("meta", {}).get("rows", [])
    if not rows:
        return _section("策略十六 內部人申報轉讓", 0, sub, _msg(r["lines"][0] if r["lines"] else "今天沒有申報"))
    body = ""
    for i, x in enumerate(rows, 1):
        mv = ctx.mv.get(x["sid"]) if ctx.mv else None
        kind = f'<span class="chip {"short" if x["side"] == "sell" else "info"}">{esc(x["kind"])}</span>'
        ratio = "-" if x["ratio"] is None else f'{x["ratio"] * 100:.1f}%'
        amt = "-" if x["amount"] is None else f'{x["amount"] / 1e8:,.2f}億'
        body += (f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, x["sid"], kind)}</td>'
                 f'<td>{"-" if not mv else format(mv / 1e8, ",.0f") + "億"}</td><td class="l">{esc(x["identity"])} {esc(x["name"])}</td>'
                 f'<td>{x["shares"] / 1e4:,.0f}萬股</td><td>{ratio}</td><td>{amt}</td></tr>')
    tbl = ('<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th>市值</th><th class="l">申報人</th>'
           f'<th>預定轉讓</th><th>占持股</th><th>約金額</th></tr></thead><tbody>{body}</tbody></table></div>'
           '<p class="foot">事前申報不等於一定會賣,申報股數是上限,申報後3日起1個月內可賣。</p>')
    return _section("策略十六 內部人申報轉讓", len(rows), sub, tbl)


def _s12(ctx, res):
    r = res.get("s12")
    if not r:
        return ""
    sub = "近26小時標題同時含負面關鍵字與上市櫃公司名稱的新聞(純規則,不經AI)。標題含公司名不代表那家公司是當事人,請自己點進去看"
    rows = r.get("meta", {}).get("rows", [])
    if not rows:
        return _section("負面新聞關鍵字掃描", 0, sub, _msg(r["lines"][0] if r["lines"] else "今天沒有"))
    body = "".join(
        f'<tr><td class="n">{i}</td><td class="l">{_stock(ctx, x["sid"], "<span class=chip>" + esc(x["keyword"]) + "</span>")}</td>'
        f'<td class="l">{esc(x["title"])}</td><td class="l">{esc(x["media"])}</td></tr>' for i, x in enumerate(rows, 1))
    tbl = ('<div class="card"><table><thead><tr><th class="l">#</th><th class="l">股票</th><th class="l">標題</th>'
           f'<th class="l">媒體</th></tr></thead><tbody>{body}</tbody></table></div>')
    return _section("負面新聞關鍵字掃描", len(rows), sub, tbl)


def _mdw(iso):
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}({'一二三四五六日'[d.weekday()]})"


def _cal(ctx, res):
    r = res.get("calendar")
    if not r:
        return ""
    m = r.get("meta", {})
    sub = r["lines"][0] if r["lines"] else "未來兩週"

    law = m.get("law", [])
    law_body = "".join(
        f'<tr><td class="l">{_mdw(x["date"])}</td><td>{esc(x["time"])}</td><td class="l">{_stock(ctx, x["sid"])}</td></tr>'
        for x in law) or '<tr><td class="l" colspan="3">這段期間沒有查到</td></tr>'
    law_card = (f'<div class="card"><div class="ct">台股法說會(市值>500億)</div><table><thead><tr><th class="l">日期</th>'
                f'<th>時間</th><th class="l">公司</th></tr></thead><tbody>{law_body}</tbody></table></div>')

    econ = [(f["start"], "FOMC", f'{_mdw(f["start"])}~{_mdw(f["end"])}', "美國", "FOMC 利率會議(決議在最後一天)", "", "")
            for f in m.get("fomc", [])]
    econ += [(e["date"], "", _mdw(e["date"]), e["country"], e["name"], e["cons"], e["prev"]) for e in m.get("econ", [])]
    econ.sort(key=lambda x: x[0])
    econ_body = "".join(
        f'<tr class="{"conf" if k else ""}"><td class="l" style="white-space:nowrap">{d}</td><td class="l" style="white-space:nowrap">{c}</td><td class="l">{esc(n)}</td>'
        f'<td>{esc(cons) or "-"}</td><td>{esc(prev) or "-"}</td></tr>'
        for _, k, d, c, n, cons, prev in econ) or '<tr><td class="l" colspan="5">這段期間沒有查到</td></tr>'
    econ_card = ('<div class="card"><div class="ct">重大經濟數據與央行(美國)</div><table><thead><tr><th class="l">日期</th>'
                 '<th class="l">國家</th><th class="l">項目</th><th>預期</th><th>前值</th></tr></thead>'
                 f'<tbody>{econ_body}</tbody></table></div>')

    earn = m.get("earnings", [])

    def earn_table(chunk):
        body = "".join(
            f'<tr><td class="l">{_mdw(x["date"])}</td><td class="l"><b>{esc(x["name"][:30])}</b> '
            f'<span class="code">{esc(x["symbol"])}</span></td><td style="white-space:nowrap">{esc(x["when"])}</td><td style="white-space:nowrap">{esc(x["eps"])}</td></tr>'
            for x in chunk)
        return (f'<div class="card"><table><thead><tr><th class="l">日期(美東)</th><th class="l">公司</th><th>時段</th>'
                f'<th>EPS預期</th></tr></thead><tbody>{body}</tbody></table></div>')

    a, b = _split(earn, 2) if earn else ([], [])
    earn_html = (f'<div class="grid g2">{earn_table(a)}{earn_table(b) if b else ""}</div>' if earn
                 else _msg("這段期間沒有查到"))

    foot = ""
    errs = m.get("errors", [])
    note = "台灣的出口訂單、主計總處 GDP、央行理監事會沒有可用的公開 API,這份不含。"
    if errs:
        note += " 資料來源問題:" + ";".join(errs)
    foot = f'<p class="foot">{esc(note)}</p>'
    return (_section("行事曆 未來兩週", None, sub, f'<div class="grid g2">{law_card}{econ_card}</div>')
            + _section("美股財報", len(earn), "市值≥2500億美元,或台灣投資人常追的半導體/AI/科技權值股", earn_html + foot))


def parse_s6(text):
    """舊格式(沒有 ◆ 分段)的解析,回傳 block 清單。新格式請用 parse_s6_sections。"""
    return [b for sec in parse_s6_sections(text)["sections"] for b in sec["blocks"]]


def parse_s6_sections(text):
    """解析策略六輸出:◆ 段名 → 區塊(利多/利空、個股、原因);沒有 ◆ 就當成單一沒有段名的段。
    回傳 {"sections": [{"name", "blocks", "none"}], "tomorrow": str|None, "notes": [str]}"""
    sections, cur_sec, cur = [], None, None
    tomorrow, notes = None, []

    def sec(name=""):
        nonlocal cur_sec
        cur_sec = {"name": name, "blocks": [], "none": False}
        sections.append(cur_sec)
        return cur_sec

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("◆"):
            sec(line.lstrip("◆").strip())
            cur = None
            continue
        if line.startswith("明日重要事件"):
            tomorrow = re.sub(r"^明日重要事件[:：]\s*", "", line)
            cur = None
            continue
        if line.startswith("(") or line.startswith("（"):
            notes.append(line)
            cur = None
            continue
        m = re.match(r"^(利多|利空)[:：]\s*(.*)$", line)
        if m:
            if cur_sec is None:
                sec("")
            cur = {"dir": m.group(1), "industry": m.group(2), "stocks": [], "reason": ""}
            cur_sec["blocks"].append(cur)
        elif cur is not None and (m := re.match(r"^個股[:：]\s*(.*)$", line)):
            cur["stocks"] = [t.strip() for t in re.split(r"[、,，]", m.group(1)) if t.strip() and t.strip() != "無"]
        elif cur is not None and (m := re.match(r"^原因[:：]\s*(.*)$", line)):
            cur["reason"] = m.group(1)
        elif cur is not None and not line.startswith("【"):
            cur["reason"] += line
        elif cur is None and cur_sec is not None and line in ("無", "无") and not cur_sec["blocks"]:
            cur_sec["none"] = True
    return {"sections": sections, "tomorrow": tomorrow, "notes": notes}


def _s6_block(b):
    tag = f'<span class="tag {"bull" if b["dir"] == "利多" else "bear"}">{b["dir"]}</span>'
    chips = ""
    for s in b["stocks"]:
        m = re.match(r"^(.*?)\((\w+|\?)\)$", s)
        chips += (f'<span class="sc">{esc(m.group(1))} <span class="code">{esc(m.group(2))}</span></span>' if m
                  else f'<span class="sc">{esc(s)}</span>')
    if not chips:
        chips = '<span class="loc">無特定個股</span>'
    return (f'<div class="item"><div><div class="t">{tag}{esc(b["industry"])}</div><div>{chips}</div></div>'
            f'<div class="why">{esc(b["reason"])}</div></div>')


def _s6(res_text):
    sub = "固定三段:供需訊號 / 總經大事 / 財報展望解讀(沒變化的段寫「無」)"
    parsed = parse_s6_sections(res_text or "")
    secs = [x for x in parsed["sections"] if x["blocks"] or x["name"]]
    if not secs:
        raw = (res_text or "今天沒有時事分析").strip()
        return _section("策略六 時事分析", None, sub, f'<div class="msg">{esc(raw)}</div>')
    body = ""
    for x in secs:
        head = f'<div class="ct" style="margin-top:6px">{esc(x["name"])}</div>' if x["name"] else ""
        if x["blocks"]:
            body += head + f'<div class="news">{"".join(_s6_block(b) for b in x["blocks"])}</div>'
        else:
            body += head + '<div class="msg">無</div>'
    foot = ""
    if parsed["tomorrow"]:
        foot += f'<p class="foot"><b>明日重要事件:</b>{esc(parsed["tomorrow"])}</p>'
    notes = [n for n in parsed["notes"] if not n.startswith("(AI")]
    if notes:
        foot += '<p class="foot">' + "<br>".join(esc(n) for n in notes) + "</p>"
    return _section("策略六 時事分析", None, sub, body + foot)


# ---------- 組裝 ----------


def summary_text(ctx, res):
    rows = _rows_of(res, "intersection")
    parts = []
    if rows:
        conf = sum(1 for r in rows if r["conflicts"])
        top = max(rows, key=lambda r: len(r["cats"]))
        parts.append(f"交集榜共 {len(rows)} 檔" + (f",其中 {conf} 檔訊號衝突(同時在策略二賣超榜、借券大增、散戶接刀或申報轉讓榜)。" if conf else "。"))
        parts.append(f"{ctx.name(top['sid'])}({top['sid']})同時入選 {len(top['cats'])} 類策略,是今天交集最多的一檔。")
    else:
        parts.append("今天沒有同時出現在兩類以上偏多策略的股票。")
    if "s3" in res:  # 策略三已停用,不在摘要裡提
        s3 = _rows_of(res, "s3")
        parts.append(f"策略三今天有 {len(s3)} 檔符合條件。" if s3 else "策略三今天沒有符合條件的股票。")
    grp = _rows_of(res, "group")
    if grp:
        top = max(grp, key=lambda g: len(g["members"]))
        parts.append(f"族群共振 {len(grp)} 個,最多檔的是{top['group']}({len(top['members'])} 檔)。")
    elif "group" in res:
        parts.append("今天沒有族群共振。")
    bears = [(k, n) for k, n in (("借券大增", len(res.get("s13", {}).get("picks", []))),
                                 ("散戶接刀", len(res.get("e_knife", {}).get("picks", []))),
                                 ("內部人申報轉讓(賣)", len(res.get("s16", {}).get("picks", [])))) if n]
    if bears:
        parts.append("空方榜:" + "、".join(f"{k} {n} 檔" for k, n in bears) + "。")
    if res.get("s14") and res["s14"].get("meta", {}).get("rows"):
        parts.append(f"集保新一週資料:大戶持股比上升 {len(res['s14']['meta']['rows'])} 檔。")
    if getattr(ctx, "mv_note", None):
        parts.append(ctx.mv_note + "。")
    return "".join(parts)


def build_html(ctx, day, res, s6_text=None, perf_text=None, notes=None):
    css = CSS.replace("__DAY__", day)
    page1 = (f'<div class="kicker">{esc(day)} 盤後</div><h1>台股盤後掃描</h1><p class="lead">{esc(summary_text(ctx, res))}</p><hr>'
             + _intersection(ctx, res) + _group(ctx, res))
    page_price = _s1(ctx, res)
    page_s2 = _s5(ctx, res) + _s2(ctx, res)
    page_inst = _s8(ctx, res) + _s9(ctx, res) + _s11(ctx, res) + _s16(ctx, res) + _s14(ctx, res)
    page_bear = _s3(ctx, res) + _s13(ctx, res) + _e_knife(ctx, res)
    page_event = _s4(ctx, res) + _s15(ctx, res) + _s12(ctx, res)
    page_geo = _s7(ctx, res) + _s10(ctx, res)
    page_cal = _cal(ctx, res)
    page_s6 = _s6(s6_text)
    if perf_text:
        page_s6 += _section("成效追蹤", None, "每週五彙總", f'<div class="card"><pre class="perf">{esc(perf_text)}</pre></div>')
    foot = ""
    if notes:
        foot = '<p class="foot">' + "<br>".join(esc(n) for n in notes) + "</p>"
    pages = [page1, page_price + page_s2, page_inst, page_bear + page_event, page_geo + page_cal, page_s6 + foot]
    pages = [p for p in pages if p.strip()]
    body = "".join(f'<div class="{"page" if i else ""}">{p}</div>' for i, p in enumerate(pages))
    return f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>台股盤後掃描 {esc(day)}</title><style>{css}</style></head><body>{body}</body></html>'


def find_browser():
    cands = [os.environ.get("CHROME_PATH"), shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
             shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("chrome"), shutil.which("msedge"),
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
    return next((c for c in cands if c and Path(c).exists()), None)


def html_to_pdf(html_text, pdf_path):
    """用無頭瀏覽器把 HTML 轉成 PDF。成功回傳 True,找不到瀏覽器或轉檔失敗回傳 False(並印出原因)。"""
    browser = find_browser()
    if not browser:
        print("  找不到 Chrome/Edge,沒辦法產生 PDF")
        return False
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()
    # ignore_cleanup_errors:Windows 上瀏覽器的子程序可能還握著暫存檔,清不掉沒關係
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        src = Path(tmp) / "report.html"
        src.write_text(html_text, encoding="utf-8")
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
               f"--user-data-dir={Path(tmp) / 'profile'}", f"--print-to-pdf={pdf_path}", src.as_uri()]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        except Exception as e:  # noqa: BLE001
            print(f"  PDF 轉檔失敗:{str(e)[:150]}")
            return False
        # 瀏覽器有時比檔案寫完還早結束(Edge 會),等檔案出現而且大小不再變
        last, stable = -1, 0
        for _ in range(60):
            size = pdf_path.stat().st_size if pdf_path.exists() else 0
            stable = stable + 1 if size and size == last else 0
            if stable >= 2:
                break
            last = size
            time.sleep(1)
    return pdf_path.exists() and pdf_path.stat().st_size > 1000
