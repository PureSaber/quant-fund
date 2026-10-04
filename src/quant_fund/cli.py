"""Local command-line workflows. No trading or account credentials."""

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

from .data import Dataset, import_observations
from .demo import create_demo
from .engine import BacktestConfig, run_backtest
from .report import export_run, verify_run
from .research import STRATEGIES, research_table


def main():
    parser = argparse.ArgumentParser(description="基金研究、FOF回测与组合监控")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="生成可复现合成数据")
    demo.add_argument("--out", default="data/demo")
    demo.add_argument("--seed", type=int, default=20260926)
    demo.add_argument("--historical-terms", action="store_true")
    check = commands.add_parser("preflight", help="只读检查数据、配置和静态账户前置条件")
    check.add_argument("--dataset", required=True)
    check.add_argument("--config", required=True)
    for name in ("validate", "research", "run"):
        command = commands.add_parser(name)
        command.add_argument("--dataset", default="data/demo")
        if name == "research":
            command.add_argument("--as-of", default="2026-08-31")
            command.add_argument("--out", default="artifacts/research.csv")
        if name == "run":
            command.add_argument("--strategy", choices=STRATEGIES)
            command.add_argument("--start")
            command.add_argument("--end")
            command.add_argument("--config", help="完整BacktestConfig JSON或已有manifest.json")
            command.add_argument("--out", required=True)
    ingest = commands.add_parser("import-nav")
    ingest.add_argument("--input", required=True)
    ingest.add_argument("--database", default="data/private/nav.sqlite")
    ingest.add_argument("--mapping", help="输入列名到标准列名的JSON映射文件")
    export = commands.add_parser("export-nav")
    export.add_argument("--database", required=True)
    export.add_argument("--out", required=True)
    verify = commands.add_parser("verify-run")
    verify.add_argument("directory")
    fetch = commands.add_parser("fetch-public")
    fetch.add_argument("--code", required=True)
    fetch.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "preflight":
        from .preflight import preflight

        print(json.dumps(preflight(args.dataset, args.config), ensure_ascii=False))
    elif args.command == "demo":
        print(create_demo(args.out, args.seed, historical_terms=args.historical_terms))
    elif args.command == "import-nav":
        mapping = (
            json.loads(Path(args.mapping).read_text(encoding="utf-8")) if args.mapping else None
        )
        print(
            json.dumps(import_observations(args.input, args.database, mapping), ensure_ascii=False)
        )
    elif args.command == "export-nav":
        output = Path(args.out)
        if output.exists():
            raise ValueError("输出已存在，请使用新的快照路径")
        with sqlite3.connect(
            f"file:{Path(args.database).resolve().as_posix()}?mode=ro", uri=True
        ) as conn:
            frame = pd.read_sql_query(
                "SELECT * FROM nav ORDER BY fund_id, nav_date, known_at", conn
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output, index=False)
        print(f"已导出{len(frame)}条历史版本")
    elif args.command == "verify-run":
        print(json.dumps(verify_run(args.directory), ensure_ascii=False))
    elif args.command == "fetch-public":
        from .providers import fetch_akshare

        print(json.dumps(fetch_akshare(args.code, args.out), ensure_ascii=False))
    else:
        dataset = Dataset.load(args.dataset)
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "funds": len(dataset.funds),
                        "observations": len(dataset.nav),
                        "classification": dataset.metadata["classification"],
                        "sha256": dataset.fingerprint,
                        "terms_mode": dataset.terms_mode,
                        "historical_terms_pit": dataset.historical_terms,
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "research":
            table, _ = research_table(dataset, args.as_of)
            output = Path(args.out)
            if output.exists():
                raise ValueError("输出已存在，请选择新路径")
            output.parent.mkdir(parents=True, exist_ok=True)
            table.to_csv(output, index=False)
            print(output)
        else:
            if args.config:
                if any(getattr(args, key) is not None for key in ("strategy", "start", "end")):
                    raise ValueError("使用--config时不要同时指定策略或日期覆盖项")
                saved = json.loads(Path(args.config).read_text(encoding="utf-8"))
                config = BacktestConfig(**saved.get("config", saved))
            else:
                config = BacktestConfig(
                    **{
                        key: getattr(args, key)
                        for key in ("strategy", "start", "end")
                        if getattr(args, key) is not None
                    }
                )
            result = run_backtest(dataset, config)
            print(export_run(result, dataset, args.dataset, args.out))


if __name__ == "__main__":
    main()
