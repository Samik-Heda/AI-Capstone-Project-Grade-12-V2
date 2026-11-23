import { useEffect } from 'react'
import { useStore } from './store.js'
import Header from './components/Header.jsx'
import ChatPanel from './components/ChatPanel.jsx'
import MapView from './components/MapView.jsx'
import RankingsCard from './components/RankingsCard.jsx'
import Legend from './components/Legend.jsx'
import WeightsPanel from './components/WeightsPanel.jsx'
import DataModal from './components/DataModal.jsx'
import DistrictPopup from './components/DistrictPopup.jsx'
import ClusterCard from './components/ClusterCard.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'
import ProfileBuilder from './components/ProfileBuilder.jsx'
import BriefModal from './components/BriefModal.jsx'

export default function App() {
  const init = useStore((s) => s.init)
  const pollHealth = useStore((s) => s.pollHealth)
  const loadError = useStore((s) => s.loadError)
  const backendDown = useStore((s) => s.backendDown)
  const health = useStore((s) => s.health)
  const modal = useStore((s) => s.ui.modal)
  const popups = useStore((s) => s.ui.popups)
  const clusterMode = useStore((s) => s.ui.clusterMode)
  const ready = useStore((s) => !!s.meta)

  useEffect(() => {
    init()
    pollHealth()
    const t = setInterval(pollHealth, 15000)
    return () => clearInterval(t)
  }, [init, pollHealth])

  return (
    <div className="app">
      <Header />
      {!backendDown && health?.code_stale && (
        <div className="banner" role="alert" data-testid="stale-banner">
          The GrowthCompass server is running older code than the files on disk, so new features may not work. Close the
          two GrowthCompass server windows and run start.bat again.
        </div>
      )}
      {backendDown && ready && (
        <div className="banner" role="alert">
          The GrowthCompass server is not responding. Your last results stay on screen; changes will work again once
          it is back (checking every 15 s).{' '}
          <button className="link-btn" onClick={pollHealth}>Check now</button>
        </div>
      )}
      <main className="panes">
        <ChatPanel />
        <section className="map-pane" aria-label="Map">
          {loadError ? (
            <div className="error-state" role="alert">
              <div>
                <p>Could not load the app data: {loadError}</p>
                <p className="muted small">Is the server running? Start it with start.bat.</p>
                <button className="btn" onClick={init}>Retry</button>
              </div>
            </div>
          ) : !ready ? (
            <div className="map-skeleton" aria-label="Loading map and rankings" aria-busy="true">
              <div className="skeleton-block" />
            </div>
          ) : (
            <>
              <MapView />
              {clusterMode ? <ClusterCard /> : <RankingsCard />}
              <Legend />
              <WeightsPanel />
            </>
          )}
        </section>
      </main>
      {ready &&
        popups.map((p, i) => (
          <ErrorBoundary key={p.id} label="this district">
            <DistrictPopup id={p.id} slot={p.slot} viaKey={p.viaKey} z={i} />
          </ErrorBoundary>
        ))}
      {ready && modal?.type === 'data' && (
        <ErrorBoundary label="the data sources" modal>
          <DataModal />
        </ErrorBoundary>
      )}
      {ready && modal?.type === 'brief' && (
        <ErrorBoundary label="the business brief" modal>
          <BriefModal />
        </ErrorBoundary>
      )}
      {ready && modal?.type === 'builder' && (
        <ErrorBoundary label="the profile builder" modal>
          <ProfileBuilder />
        </ErrorBoundary>
      )}
    </div>
  )
}
