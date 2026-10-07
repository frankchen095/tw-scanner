"""
main.py —— 主程式:抓資料 → 算各策略 → 組成報表 → 用 LINE 推播

推播順序:交集榜 → 策略一~五 → 策略八、九 → 策略六(時事分析)
(策略七地緣分點在第二階段加入;成效追蹤在第三階段加入,只在每週五推播)

舊的 功能1(大摩+法人排行)、功能2(外資投信同買)、功能4(帶量突破盤整)已停用,
程式檔案還留在 scripts/reports/,沒有被呼叫。

用法:
    python scripts/main.py                       # 用今天日期跑,抓資料 + 推播 LINE
    python scripts/main.py --date 2026-10-06      # 指定日期(補跑、測試用)
    python scripts/main.py --date 2026-10-06 --no-fetch   # 跳過抓資料,只用資料庫現有資料出報表
    python scripts/main.py --date 2026-10-06 --dry-run    # 不推播,只印在畫面上看結果
    python scripts/main.py --dry-run --no-news            # 跳過策略六(不需要 ANTHROPIC_API_KEY)
"""

import argparse
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context import Context  # noqa: E402
from db import connect  # noqa: E402
from fetchers import do_fetch  # noqa: E402
from geo import refresh_geo  # noqa: E402
from mops_buyback import refresh_programs  # noqa: E402
from notify import pack, send_line  # noqa: E402
from risk_tags import RiskTags  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "reports"))
import feature3_news as s6_news  # noqa: E402
import intersection  # noqa: E402
import s1_s5_highs  # noqa: E402
import s2_brokers  # noqa: E402
import s3_limit_up  # noqa: E402
import s4_revenue  # noqa: E402
import s7_geo_brokers  # noqa: E402
import s8_s9_institutional  # noqa: E402

DISCLAIMER = "以上為程式依公開資料自動整理的篩選結果,不是投資建議;歷史命中率不代表未來表現。"


def render(r):
    return r["title"] + "\n" + "\n".join(r["lines"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--no-fetch", action="store_true", help="跳過抓資料,直接用資料庫現有資料出報表")
    parser.add_argument("--dry-run", action="store_true", help="不推播,只印在畫面上")
    parser.add_argument("--no-news", action="store_true", help="跳過策略六(時事分析),測試時省時間")
    parser.add_argument("--force", action="store_true", help="這天已經推播過也再推一次")
    args = parser.parse_args()
    day = args.date

    conn = connect()

    already = conn.execute("SELECT sent_at FROM pushed_reports WHERE date=?", (day,)).fetchone()
    if already and not args.dry_run and not args.force:
        print(f"\n{day} 的報表已經在 {already[0]} 推播過了,不重複推播。(要重新推播請加 --force)")
        conn.close()
        return

    geo_error = refresh_geo(conn)  # 公司/分點地址對照表,每月更新一次;失敗就沿用舊的
    buyback_error = refresh_programs(conn)  # 庫藏股買回計畫(標『庫藏股執行中』用),每天更新一次

    if not args.no_fetch:
        if not do_fetch(conn, day):
            print(f"\n{day} 沒有行情資料(非交易日?),不推播。")
            return
    elif not conn.execute("SELECT 1 FROM daily_price WHERE date=? LIMIT 1", (day,)).fetchone():
        print(f"\n資料庫裡沒有 {day} 的行情,不產生報表。")
        return

    print(f"\n=== 載入風險標記資料(處置/注意/除權息/法說/融券) ===")
    risk = RiskTags(day)
    print(
        f"  處置 {len(risk.disposition)} 檔、注意 {len(risk.attention)} 檔、"
        f"未來5日除權息 {len(risk.exdiv)} 檔、未來5日法說 {len(risk.law_conf)} 檔、融券限制 {len(risk.short_status)} 檔"
    )
    ctx = Context(conn, day, risk)
    missing_note = None
    if ctx.big is None:
        missing_note = ("FinMind 完全沒有可用的市值表資料,所以需要『市值>500億』條件的策略"
                        "(策略一、三、四、五、七同區、八、九)今天沒有結果")

    print(f"\n=== 計算 {day} 的各策略 ===")
    res, failures = {}, []
    for name, mod in [
        ("策略一/五", s1_s5_highs),
        ("策略二", s2_brokers),
        ("策略三", s3_limit_up),
        ("策略四", s4_revenue),
        ("策略七", s7_geo_brokers),
        ("策略八/九", s8_s9_institutional),
    ]:
        try:
            print(f"  {name}…")
            res.update(mod.build(ctx))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            failures.append(f"{name} 執行失敗:{str(e)[:150]}")

    sections = [f"台股盤後掃描 {day}"]
    sections.append(render(intersection.build(ctx, res)))
    for key in ("s1", "s2_buy", "s2_sell", "s2_buy3", "s3", "s4", "s5", "s7", "s8_1", "s8_5", "s9_trust", "s9_foreign"):
        if key in res:
            sections.append(render(res[key]))

    if not args.no_news:
        print("  策略六(時事分析)…")
        try:
            sections += s6_news.build(conn, day)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            failures.append(f"策略六 執行失敗:{str(e)[:150]}")

    notes = []
    for e in (geo_error, buyback_error):
        if e:
            risk.errors.append(e)
    if risk.errors:
        notes.append("風險標記資料來源今天有問題,以下標記可能缺漏:\n- " + "\n- ".join(risk.errors))
    if ctx.mv_note:
        notes.append(ctx.mv_note)
    if missing_note:
        failures.insert(0, missing_note)
    if failures:
        notes.append("今天執行失敗的策略:\n- " + "\n- ".join(failures))
    notes.append(DISCLAIMER)
    sections.append("\n\n".join(notes))

    bubbles = pack(sections)
    ok = True
    if args.dry_run:
        for s in sections:
            print("\n" + "=" * 60)
            print(s)
        print("\n" + "=" * 60)
        print(f"(合併後共 {len(bubbles)} 則 LINE 訊息,字數 {[len(b) for b in bubbles]})")
    else:
        sent = send_line(bubbles)
        print(f"\nLINE 已送出 {sent}/{len(bubbles)} 則訊息")
        ok = sent == len(bubbles)
        if ok:
            conn.execute(
                "INSERT OR REPLACE INTO pushed_reports VALUES (?,?,?)",
                (day, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(bubbles)),
            )
            conn.commit()

    conn.close()
    print("\n完成。" if ok else "\n推播沒有全部送出,請看上面的錯誤訊息。")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
