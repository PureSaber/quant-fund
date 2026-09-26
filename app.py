from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from quant_fund.data import Dataset, import_observations, validate_nav
from quant_fund.engine import BacktestConfig, run_backtest
from quant_fund.monitor import monitor
from quant_fund.report import export_run
from quant_fund.research import STRATEGIES, latest_complete_interval, performance, research_table

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="基金研究工作台", page_icon="◈", layout="wide")
st.markdown(
    """<style>
.block-container{padding-top:2rem;max-width:1500px}h1{letter-spacing:-1px}
[data-testid="stMetric"]{background:#f1f6f7;padding:16px;border-radius:10px}
[data-testid="stMetricValue"]{font-size:1.35rem}
[data-testid="stSidebar"]{background:#edf2f4}.stTabs [data-baseweb="tab-list"]{gap:28px}
@media(max-width:900px){
[data-testid="stHorizontalBlock"]:has([data-testid="stPlotlyChart"]){flex-wrap:wrap}
[data-testid="stHorizontalBlock"]:has([data-testid="stPlotlyChart"])>[data-testid="stColumn"]{min-width:100%}
}
</style>""",
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def cached_research(directory, fingerprint, date):
    return research_table(Dataset.load(directory), date)


def chart(frame, **kwargs):
    fig = px.line(
        frame, **kwargs, color_discrete_sequence=["#087f8c", "#d58a31", "#5367a8", "#968273"]
    )
    fig.update_layout(
        template="plotly_white",
        height=360,
        margin={"l": 10, "r": 10, "t": 35, "b": 20},
        legend={"orientation": "h", "y": -0.2},
        xaxis_title="日期",
        yaxis_title="累计净值",
        legend_title_text="",
    )
    return fig


with st.sidebar:
    st.title("基金研究工作台")
    directory = st.text_input("数据目录", str(ROOT / "data" / "demo"))
    st.caption("公募FOF · 私募证券基金FOF")
try:
    dataset = Dataset.load(directory)
except (ValueError, OSError, KeyError) as error:
    st.error(f"数据尚未就绪：{error}")
    st.code("quant-fund demo --out data/demo")
    st.stop()

with st.sidebar:
    default_date = pd.Timestamp(
        dataset.metadata.get("default_as_of", pd.Timestamp.today().normalize())
    )
    default_date = max(dataset.calendar.min(), min(default_date, dataset.calendar.max()))
    as_of = st.date_input(
        "研究时点",
        value=default_date.date(),
        min_value=dataset.calendar.min().date(),
        max_value=dataset.calendar.max().date(),
    )
    kinds = st.multiselect(
        "基金类型",
        ["public", "private", "etf"],
        default=["public", "private"],
        format_func=lambda x: {"public": "公募", "private": "私募", "etf": "ETF"}[x],
    )
    st.caption("研究频率：月度；不将私募月净值插值成日频收益。")
st.title("基金研究与组合配置")
classification = dataset.metadata["classification"]
if classification == "synthetic":
    st.info("合成数据演示：用于验证研究与账本流程，图中数值不代表真实基金表现。")
else:
    st.caption(f"数据分类：{classification} · 获知口径：{dataset.metadata['known_at_policy']}")

table, panel = cached_research(directory, dataset.fingerprint, str(as_of))
view = table.loc[table.kind.isin(kinds)] if not table.empty else table
metrics = st.columns(4)
metrics[0].metric("研究池", len(view))
metrics[1].metric("管理人", view.manager.nunique() if not view.empty else 0)
metrics[2].metric("可研究基金", int(view.eligible.sum()) if not view.empty else 0)
metrics[3].metric("研究时点", str(as_of))
research, backtest, monitoring, data_tab = st.tabs(
    ["基金研究", "组合回测", "组合监控", "数据与导入"]
)

with research:
    options = view.fund_id.tolist() if not view.empty else []
    chosen = st.multiselect(
        "比较基金", options, default=options[:4], format_func=lambda c: dataset.funds[c].name
    )
    if chosen:
        visible = panel[[c for c in chosen if c in panel]]
        if not visible.empty:
            left, right = st.columns([1.4, 1])
            with left:
                st.subheader("月度收益路径")
                # No cumprod across unknown observations: show only the common complete interval.
                complete = latest_complete_interval(visible)
                if not complete.empty:
                    wealth = (1 + complete).cumprod()
                    st.plotly_chart(
                        chart(wealth.rename(columns={c: dataset.funds[c].name for c in wealth})),
                        width="stretch",
                    )
                    st.caption(
                        "最近连续完整共同月份的分红再投资收益累积；首个点包含首月收益，缺失月份不跨越连接。"
                    )
            with right:
                st.subheader("月度收益相关性")
                correlation = visible.rename(
                    columns={c: dataset.funds[c].name for c in visible}
                ).corr(min_periods=12)
                st.plotly_chart(
                    px.imshow(
                        correlation,
                        zmin=-1,
                        zmax=1,
                        color_continuous_scale="RdBu_r",
                        text_auto=".2f",
                        aspect="auto",
                    ),
                    width="stretch",
                )
                st.caption("至少12个共同月份；空值代表样本不足。")
    st.subheader("基金比较")
    display = view.copy()
    for col in ("annual_return", "volatility", "max_drawdown", "peer_percentile"):
        if col in display:
            display[col] = display[col].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
    preferred = [
        "name",
        "kind",
        "strategy",
        "annual_return",
        "volatility",
        "max_drawdown",
        "observations",
        "history_start",
        "history_end",
        "peer_percentile",
        "peer_count",
    ]
    if not display.empty:
        display = display[preferred + [c for c in display if c not in preferred]]
    st.dataframe(
        display.rename(
            columns={
                "name": "基金",
                "kind": "类型",
                "strategy": "策略",
                "annual_return": "年化收益",
                "volatility": "年化波动",
                "max_drawdown": "最大回撤",
                "observations": "月度样本数",
                "history_start": "样本首月",
                "history_end": "样本末月",
                "peer_percentile": "同类百分位",
                "peer_count": "同类数量",
                "manager": "管理人",
                "eligible": "存续且已获知",
                "stale_days": "估值滞后天数",
                "nav_date": "净值日期",
            }
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "指标使用各基金最近连续完整月份。同类百分位要求类型、策略和样本起止均相同，至少两只；夏普使用零无风险利率。"
    )
    st.download_button(
        "导出当前基金比较", view.to_csv(index=False).encode("utf-8-sig"), "fund-research.csv"
    )

with backtest:
    st.subheader("滚动选基与场外申赎")
    with st.form("backtest"):
        a, b, c = st.columns(3)
        method = a.selectbox("配置方法", list(STRATEGIES), index=2, format_func=STRATEGIES.get)
        start_default = pd.Timestamp(
            dataset.metadata.get("default_start", pd.Timestamp(as_of) - pd.DateOffset(years=2))
        )
        start_default = max(dataset.calendar.min(), min(start_default, pd.Timestamp(as_of)))
        start = b.date_input("回测开始", start_default.date())
        end = c.date_input("回测结束", as_of)
        a, b, c = st.columns(3)
        max_weight = a.slider("单基金目标上限", 0.2, 1.0, 0.4, 0.05)
        buffer = b.slider("目标现金比例", 0.0, 0.3, 0.05, 0.01)
        compare = c.checkbox("同时计算等权对照", value=True)
        execute = st.form_submit_button("运行回测", type="primary")
    st.caption(
        "每月首个交易日收盘决策，下一可申请日执行；私募开放日与锁定期按产品条款模拟。"
        "上方类型筛选用于研究视图，回测使用数据集中的全部合格基金。"
    )
    if execute:
        try:
            config = BacktestConfig(
                str(start), str(end), method, max_weight=max_weight, cash_buffer=buffer
            )
            with st.spinner("正在计算逐日账本与滚动配置…"):
                result = run_backtest(dataset, config)
                baseline = (
                    run_backtest(dataset, BacktestConfig(**{**asdict(config), "strategy": "equal"}))
                    if compare
                    else None
                )
            st.session_state["run"] = (dataset.fingerprint, result, baseline)
            st.session_state.pop("exported_run", None)
            st.success("回测完成；详情可在组合监控查看。")
        except (ValueError, RuntimeError) as error:
            st.error(str(error))
    saved = st.session_state.get("run")
    if saved and saved[0] == dataset.fingerprint:
        _, result, baseline = saved
        curve = result["nav"][["nav"]].rename(
            columns={"nav": STRATEGIES[result["config"]["strategy"]]}
        )
        if baseline:
            curve["等权对照"] = baseline["nav"].nav
        nav = result["nav"].nav
        monthly = nav.resample("ME").last().pct_change(fill_method=None)
        score = performance(monthly.loc[monthly.index <= nav.index[-1]], 12)
        a, b, c, d = st.columns(4)
        a.metric("区间收益", f"{nav.iloc[-1] / nav.iloc[0] - 1:.2%}")
        b.metric("估值曲线最大回撤", f"{(nav / nav.cummax() - 1).min():.2%}")
        c.metric(
            "月度年化波动",
            f"{score['volatility']:.2%}" if score["volatility"] is not None else "样本不足",
        )
        d.metric("已确认申赎", len(result["trades"]))
        st.plotly_chart(chart(curve), width="stretch")
        st.caption("低频私募净值沿用最近已知估值；日度曲线的平滑不表示实际低风险。")
        with st.expander("决策、指令与资金账本"):
            st.dataframe(result["decisions"], hide_index=True)
            st.dataframe(result["orders"], hide_index=True)
            st.dataframe(result["nav"])
            st.dataframe(result["events"], hide_index=True)
        if st.button("导出完整研究报告"):
            destination = ROOT / "artifacts" / pd.Timestamp.now().strftime("run-%Y%m%d-%H%M%S-%f")
            export_run(result, dataset, directory, destination)
            st.session_state["exported_run"] = (dataset.fingerprint, str(destination))
        exported = st.session_state.get("exported_run")
        if exported and exported[0] == dataset.fingerprint:
            destination = Path(exported[1])
            st.success(f"已导出：{destination}")
            st.download_button(
                "下载独立HTML报告", (destination / "report.html").read_bytes(), "fof-report.html"
            )

with monitoring:
    saved = st.session_state.get("run")
    if not saved or saved[0] != dataset.fingerprint:
        st.info("先在组合回测中运行一个组合，即可查看持仓、风险和资金日历。")
    else:
        result = saved[1]
        monitoring_data = monitor(result, dataset)
        st.caption(
            f"监控时点：{result['ledger'].current_date.date()}，对应当前回测期末；不会读取未来净值。"
        )
        st.dataframe(monitoring_data["alerts"], hide_index=True, width="stretch")
        left, right = st.columns(2)
        with left:
            st.subheader("管理人集中度")
            concentration = monitoring_data["concentration"]
            if not concentration.empty:
                st.plotly_chart(
                    px.bar(
                        concentration,
                        x="weight",
                        y="manager",
                        orientation="h",
                        color_discrete_sequence=["#087f8c"],
                    ),
                    width="stretch",
                )
        with right:
            st.subheader("风险贡献")
            risk = monitoring_data["risk"]
            if not risk.empty:
                st.plotly_chart(
                    px.bar(
                        risk.reset_index(names="fund_id"),
                        x="fund_id",
                        y="risk_share",
                        color_discrete_sequence=["#5367a8"],
                    ),
                    width="stretch",
                )
            st.caption("qis计算Euler波动贡献；缺少持仓收益历史时显示不可计算。")
        st.subheader("资金可用日历")
        st.dataframe(monitoring_data["liquidity"], hide_index=True, width="stretch")
        st.subheader("私募风格暴露与变化")
        st.caption("以公募股债篮子为代理因子；比较最近12个月与前移3个月的回归，不能还原真实底仓。")
        st.dataframe(monitoring_data["styles"], hide_index=True, width="stretch")
        st.subheader("实际持仓")
        st.dataframe(monitoring_data["holdings"], hide_index=True, width="stretch")
        st.subheader("调仓差额复核清单")
        st.caption(
            "沿用最近一次成功配置的目标，展示当前差额与限制；各行增配金额未联合分配现金，不是可直接执行的订单。"
        )
        st.dataframe(monitoring_data["rebalance_review"], hide_index=True, width="stretch")
        for key in ("holdings", "liquidity", "risk"):
            st.download_button(
                f"导出{key}",
                monitoring_data[key].to_csv().encode("utf-8-sig"),
                f"{key}.csv",
                key=key,
            )

with data_tab:
    st.subheader("数据来源与历史版本")
    st.json(dataset.metadata)
    st.code(f"SHA-256: {dataset.fingerprint}")
    st.caption(
        "known_at是这条数据首次能够用于决策的日期。今天补录的历史净值不能伪装成过去已知。"
        "单位净值用于份额账本，total_return_nav用于收益研究；不接收累计净值代替复权净值。"
    )
    st.dataframe(dataset.as_of(as_of).tail(100), hide_index=True, width="stretch")
    st.subheader("CSV/Excel净值导入")
    upload = st.file_uploader("选择净值文件", type=["csv", "xlsx"])
    mapping_text = st.text_area(
        "字段映射JSON（可留空）", placeholder='{"基金代码":"fund_id","净值日期":"nav_date"}'
    )
    if upload:
        try:
            mapping = json.loads(mapping_text) if mapping_text.strip() else {}
            incoming = (
                pd.read_csv(upload, dtype=str)
                if upload.name.lower().endswith(".csv")
                else pd.read_excel(upload, dtype=str)
            )
            normalized = validate_nav(incoming.rename(columns=mapping))
            st.dataframe(normalized.head(20), hide_index=True)
            database = st.text_input(
                "历史版本数据库", str(ROOT / "data" / "private" / "nav.sqlite")
            )
            if st.button("写入历史版本库"):
                with tempfile.TemporaryDirectory() as temporary:
                    file = Path(temporary) / "normalized.csv"
                    normalized.to_csv(file, index=False)
                    imported = import_observations(file, database)
                st.success(f"已导入{imported['inserted']}行，重复记录{imported['unchanged']}行。")
            st.download_button(
                "下载标准化净值CSV", normalized.to_csv(index=False).encode("utf-8-sig"), "nav.csv"
            )
            st.caption(
                "导入独立历史库，不会覆盖当前研究数据集。配齐基金主表、日历与分红表后，再切换研究目录。"
            )
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            st.error(str(error))
