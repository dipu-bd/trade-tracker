"""Run one portfolio scan from the command line.

    python jobs/portfolio_scan.py --list
    python jobs/portfolio_scan.py --create "Swing Book" --capital 10000
    python jobs/portfolio_scan.py --portfolio 1 --sleeve crypto --dry-run

A dry run scores and plans without touching cash or positions, and works
against the crypto sleeve with no API keys at all.
"""

import argparse
import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Scan the market and update a paper portfolio',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--portfolio', type=int, help='Portfolio id to scan')
    parser.add_argument('--name', help='Portfolio name (alternative to --portfolio)')
    parser.add_argument(
        '--sleeve', default='all', choices=['all', 'equity', 'crypto'],
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Plan and print, but do not record any trades',
    )
    parser.add_argument('--list', action='store_true', help='List portfolios and exit')
    parser.add_argument('--create', metavar='NAME', help='Create a portfolio and exit')
    parser.add_argument('--capital', type=float, default=10_000)
    parser.add_argument('--crypto-max-pct', type=float, default=None)
    parser.add_argument('--risk-pct', type=float, default=None)
    parser.add_argument('--digest', action='store_true', help='Send the digest email')
    parser.add_argument('--json', metavar='PATH', help='Write the result as JSON')
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    from marketbot.context import ServerContext
    ctx = ServerContext()

    if args.list:
        with ctx.db.session() as session:
            for portfolio in ctx.portfolios.list_all(session):
                state = 'active' if portfolio.is_active else 'paused'
                print(
                    f'{portfolio.id:>3}  {portfolio.name:<28} '
                    f'cash {portfolio.cash:>12,.2f}  {state}'
                )
        return

    if args.create:
        fields = {'name': args.create, 'initial_capital': args.capital}
        if args.crypto_max_pct is not None:
            fields['crypto_max_pct'] = args.crypto_max_pct
        if args.risk_pct is not None:
            fields['risk_pct_per_trade'] = args.risk_pct
        with ctx.db.session() as session:
            portfolio = ctx.portfolios.create(session, **fields)
            print(f'Created portfolio {portfolio.id}: {portfolio.name}')
        return

    portfolio_id = args.portfolio
    if portfolio_id is None and args.name:
        with ctx.db.session() as session:
            portfolio = ctx.portfolios.get_by_name(session, args.name)
            portfolio_id = portfolio.id if portfolio else None
    if portfolio_id is None:
        sys.exit('Specify --portfolio <id> or --name <name> (or use --list)')

    if args.digest:
        print(f'Digest sent: {ctx.engine.send_digest(portfolio_id)}')
        return

    result = ctx.engine.run_scan(
        portfolio_id, sleeve=args.sleeve, dry_run=args.dry_run
    )
    output = json.dumps(result, indent=2, default=str)
    print(output)

    if args.json:
        with open(args.json, 'w', encoding='utf-8') as fh:
            fh.write(output)
        print(f'Wrote {args.json}', file=sys.stderr)


if __name__ == '__main__':
    main()
