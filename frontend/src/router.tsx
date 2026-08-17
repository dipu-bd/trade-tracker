import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  useParams,
  useSearch,
} from '@tanstack/react-router'

import { usePortfolios } from '@/api/hooks'
import { Shell } from '@/Shell'
import { AICallLog } from '@/pages/AICallLog'
import { Backtest } from '@/pages/Backtest'
import { AnalystChat } from '@/pages/Chat'
import { MarketExplorer } from '@/pages/Market'
import { EventFeed, PriceTracking, ProviderHealthPage } from '@/pages/Observability'
import { Blotter, PortfolioDetailPage, PortfolioList } from '@/pages/Portfolios'
import { Settings } from '@/pages/Settings'
import { StrategyPage } from '@/pages/Strategy'

const rootRoute = createRootRoute({ component: Shell })

function scoped(path: string, render: (id: number) => React.ReactElement) {
  return createRoute({
    getParentRoute: () => rootRoute,
    path: `/portfolios/$portfolioId/${path}`,
    component: function Scoped() {
      const { portfolioId } = useParams({ strict: false })
      return render(Number(portfolioId))
    },
  })
}

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/portfolios' })
  },
})

const portfoliosRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/portfolios',
  component: PortfolioList,
})

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/settings',
  validateSearch: (search: Record<string, unknown>) => ({
    portfolio: search.portfolio ? Number(search.portfolio) : undefined,
  }),
  component: function SettingsRoute() {
    const { portfolio } = useSearch({ strict: false }) as { portfolio?: number }
    const portfolios = usePortfolios()
    return <Settings portfolioId={portfolio ?? portfolios.data?.[0]?.id ?? null} />
  },
})

const globalRoutes = [
  createRoute({ getParentRoute: () => rootRoute, path: '/market', component: MarketExplorer }),
  createRoute({ getParentRoute: () => rootRoute, path: '/prices', component: PriceTracking }),
  createRoute({ getParentRoute: () => rootRoute, path: '/providers', component: ProviderHealthPage }),
  createRoute({ getParentRoute: () => rootRoute, path: '/events', component: EventFeed }),
]

const routeTree = rootRoute.addChildren([
  indexRoute,
  portfoliosRoute,
  settingsRoute,
  ...globalRoutes,
  scoped('detail', (id) => <PortfolioDetailPage id={id} />),
  scoped('blotter', (id) => <Blotter id={id} />),
  scoped('strategy', (id) => <StrategyPage id={id} />),
  scoped('ai', (id) => <AICallLog portfolioId={id} />),
  scoped('backtest', (id) => <Backtest id={id} />),
  scoped('chat', (id) => <AnalystChat id={id} />),
])

export const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
