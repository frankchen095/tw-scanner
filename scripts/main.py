"""
main.py —— 主程式:抓資料 → 算各策略 → 組成報表 → 用 LINE 推播

推播順序(每段標 §編號,方便在手機上找):
  §1 交集榜 → §2 族群訊號 → 價格位置(策略一、五)→ 策略二 → 法人(策略八、九)→ 質押(策略十一,月頻,只在新一個月資料入庫日)→
  內部人申報轉讓(策略十六)→ 大戶持股比(策略十四,只在集保新一週資料入庫日)→
  空方區(借券 策略十三、散戶接刀榜;策略三已停用)→ 事件區(策略四、毛利率轉折 策略十五、基本面動能榜、負面新聞)→
  策略七 → 策略十(美日族群創新高)→ 行事曆 → 策略六(時事分析)→ 成效追蹤(只在每週五)
每次正式推播會把各策略選出的股票記進 signals 表,每天更新滿 5/20 個交易日的報酬(見 tracking.py)

舊的 功能1(大摩+法人排行)、功能2(外資投信同買)、功能4(帶量突破盤整)已停用,
程式檔案還留在 scripts/reports/,沒有被呼叫。

用法:
    python scripts/main.py                       # 用今天日期跑,抓資料 + 推播 LINE
    python scripts/main.py --date 2026-10-06      # 指定日期(補跑、測試用)
    python scripts/main.py --date 2026-10-06 --no-fetch   # 跳過抓資料,只用資料庫現有資料出報表
    python scripts/main.py --date 2026-10-06 --dry-run    # 不推播,只印在畫面上看結果
    python scripts/main.py --dry-run --no-news            # 跳過策略六(不需要 ANTHROPIC_API_KEY)
    python scripts/main.py --dry-run --show-performance   # 不是週五也把成效追蹤彙總印出來
    python scripts/main.py --dry-run --show-all           # 『只在特定日子才出現』的段落(集保週資料、動能榜)也強制顯示
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
import pdf_report  # noqa: E402
import publish  # noqa: E402
from risk_tags import RiskTags  # noqa: E402
from tracking import record_signals, summary_text, update_returns  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "reports"))
import feature3_news as s6_news  # noqa: E402
import intersection  # noqa: E402
import s1_s5_highs  # noqa: E402
import s2_brokers  # noqa: E402
# 策略三(漲停放空)已於 2026-10-19 停用:回測 20 日放空平均虧 9.6%、勝率 38%(scripts/reports/s3_limit_up.py 還留著,沒有被呼叫)
import s4_revenue  # noqa: E402
import s7_geo_brokers  # noqa: E402
import s8_s9_institutional  # noqa: E402
import s10_global_highs  # noqa: E402
import s11_pledge  # noqa: E402
import calendar_events  # noqa: E402
import group_resonance  # noqa: E402
import margin_notes  # noqa: E402
import s12_negative_news  # noqa: E402
import s13_short_lending  # noqa: E402
import s14_big_holders  # noqa: E402
import s15_margin_turn  # noqa: E402
import s16_insider_transfer  # noqa: E402

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
    parser.add_argument("--no-pdf", action="store_true", help="不產生 PDF,直接用文字訊息推播")
    parser.add_argument("--show-all", action="store_true", help="集保週資料、動能榜這種只在特定日子出現的段落也強制顯示")
    parser.add_argument("--show-performance", action="store_true", help="不是週五也附上成效追蹤彙總")
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
    risk.load_db(conn, s14_big_holders.weekly_flags)  # 〔大戶增〕〔大戶減〕〔質押比高〕
    ctx = Context(conn, day, risk)
    ctx.dry_run, ctx.show_all = args.dry_run, args.show_all
    missing_note = None
    if ctx.big is None:
        missing_note = ("FinMind 完全沒有可用的市值表資料,所以需要『市值>500億』條件的策略"
                        "(策略一、三、四、五、七同區、八、九)今天沒有結果")

    print(f"\n=== 計算 {day} 的各策略 ===")
    res, failures = {}, []

    def run(name, mod, *extra):
        try:
            print(f"  {name}…")
            res.update(mod.build(ctx, *extra))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            failures.append(f"{name} 執行失敗:{str(e)[:150]}")

    for name, mod in [
        ("策略一/五", s1_s5_highs),
        ("策略二", s2_brokers),
        ("策略四", s4_revenue),
        ("策略七", s7_geo_brokers),
        ("策略八/九", s8_s9_institutional),
        ("散戶接刀榜", margin_notes),
        ("策略十三 借券", s13_short_lending),
        ("策略十四 大戶持股", s14_big_holders),
        ("策略十五 財報/動能榜", s15_margin_turn),
        ("策略十一 質押(月頻)", s11_pledge),
        ("策略十六 內部人轉讓", s16_insider_transfer),
        ("策略十", s10_global_highs),
        ("行事曆", calendar_events),
    ]:
        run(name, mod)
    try:  # 融資附註:策略一/五〔融資未增〕(策略三已停用,不再補融資使用率)
        margin_notes.apply_clean_tags(ctx, res)
        margin_notes.apply_s3_usage(ctx, res)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        failures.append(f"融資附註執行失敗:{str(e)[:150]}")
    if not args.no_news:
        run("策略十二 負面新聞", s12_negative_news)

    inter = intersection.build(ctx, res)
    try:
        margin_notes.apply_clean_tags(ctx, {"intersection": inter})
    except Exception:  # noqa: BLE001
        pass
    res["intersection"] = inter
    run("族群訊號", group_resonance, res)

    # 推播順序(章節編號會自動加在每段標題前)
    ORDER = [
        "intersection", "group",
        "s1", "s5",
        "s2_buy", "s2_sell", "s2_buy3",
        "s8_1", "s8_5", "s9_trust", "s9_foreign",
        "s11", "s11_release",
        "s16",
        "s14", "s14_down",
        "s13", "s13_cover", "e_knife",
        "s4", "s15", "s15_weak", "s15_mom", "s15_mom2", "s12",
        "s7", "s10", "calendar",
    ]
    sections = [f"台股盤後掃描 {day}"]
    n_sec = 0
    for key in ORDER:
        if key in res:
            n_sec += 1
            sections.append(f"§{n_sec} " + render(res[key]))

    s6_sections = []
    perf_text = None
    if not args.no_news:
        print("  策略六(時事分析)…")
        try:
            s6_sections = s6_news.build(conn, day)
            n_sec += 1
            sections += [f"§{n_sec} " + x for x in s6_sections]
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            failures.append(f"策略六 執行失敗:{str(e)[:150]}")

    # 成效追蹤:正式推播才記錄今天的訊號(dry-run 不記,免得測試資料混進去);每天更新到期的報酬
    if not args.dry_run:
        try:
            print(f"  成效追蹤:記錄 {record_signals(conn, day, res)} 筆訊號")
            group_resonance.record_groups(conn, day, res)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            failures.append(f"成效追蹤記錄訊號失敗:{str(e)[:150]}")
    try:
        n_new, err = update_returns(conn, day)
        print(f"  成效追蹤:新算出 {n_new} 筆報酬")
        if err:
            risk.errors.append(err)
        if date.fromisoformat(day).weekday() == 4 or args.show_performance:
            perf_text = summary_text(conn, day)
            n_sec += 1
            sections.append(f"§{n_sec} " + perf_text)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        failures.append(f"成效追蹤更新失敗:{str(e)[:150]}")

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

    # 先試著做 PDF 並發佈成網址;成功就只推「摘要+連結」,任何一步失敗就退回原本的文字訊息
    pdf_url, pdf_summary = None, None
    if not args.no_pdf:
        try:
            full = res
            pdf_summary = pdf_report.summary_text(ctx, full)
            html = pdf_report.build_html(
                ctx, day, full, s6_text="\n".join(s6_sections) or None, perf_text=perf_text, notes=notes,
            )
            if args.dry_run:
                out = Path(__file__).resolve().parent.parent / "out" / f"{day}.pdf"
            else:
                out = publish.pdf_path_for(day)
            if pdf_report.html_to_pdf(html, out):
                print(f"  PDF 已產生:{out}")
                pdf_url = publish.pdf_url_for(day) if args.dry_run else publish.publish(day)
            else:
                print("  PDF 沒有產生,改用文字訊息")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            print("  PDF 流程出錯,改用文字訊息")

    if pdf_url:
        extra = [n for n in notes if n != DISCLAIMER and n != ctx.mv_note]
        text = f"台股盤後掃描 {day}\n{pdf_summary}\n\n完整報表(PDF):\n{pdf_url}"
        if extra:
            text += "\n\n" + "\n\n".join(extra)
        bubbles = pack([text])
    else:
        bubbles = pack(sections)
    ok = True
    if args.dry_run:
        for s in (bubbles if pdf_url else sections):
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
