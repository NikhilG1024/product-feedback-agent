import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ChevronDown,
  LayoutGrid,
  MessageSquare,
  Plug,
  Search,
  LogOut,
} from "lucide-react";
import type { Api, Product } from "./types";
import { HttpApi, errorMessage } from "./api";
import { waitForBackend, openDemoSession } from "./startup";
import { DemoApi } from "./demo";
import { Dashboard } from "./Dashboard";
import { SummaryDashboard } from "./SummaryDashboard";
import { ProductImage } from "./ProductImage";
import { Reviewer } from "./Reviewer";
import { ErrorNotice, Loading, Modal } from "./ui";
function Connection({
  onClose,
  onConnect,
}: {
  onClose: () => void;
  onConnect: (api: Api) => void;
}) {
  const [token, setToken] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function connect() {
    setBusy(true);
    setError("");
    const next = new HttpApi(token.trim());
    try {
      await waitForBackend(new AbortController().signal);
      await next.products();
      onConnect(next);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Connect to your workspace"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="muted">
        Enter the access token provided by your demo host. Your server decides
        which actions this account can perform.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void connect();
        }}
      >
        <label className="field">
          Access token
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            required
            disabled={busy}
          />
        </label>
        <p className="fine-print">
          Held only in this page’s memory. Refreshing or disconnecting removes
          it. Model and Hindsight keys stay on the server.
        </p>
        {error && <ErrorNotice message={error} />}
        <button
          className="button primary full"
          disabled={busy || !token.trim()}
        >
          {busy ? "Connecting…" : "Connect"}
        </button>
      </form>
    </Modal>
  );
}
function Workspace({
  api,
  onConnect,
  onDisconnect,
}: {
  api: Api;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const [products, setProducts] = useState<Product[]>([]),
    [selected, setSelected] = useState<Product | null>(null),
    [cursor, setCursor] = useState<string | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [picker, setPicker] = useState(false),
    [query, setQuery] = useState(""),
    [category, setCategory] = useState("all"),
    [view, setView] = useState<"pm" | "reviewer" | "legacy">("pm"),
    [reviewVersion, setReviewVersion] = useState(0);
  const [connectionNotice, setConnectionNotice] = useState(true);
  useEffect(() => {
    setConnectionNotice(true);
    if (api.demo) return;
    const timer = setTimeout(() => setConnectionNotice(false), 4000);
    return () => clearTimeout(timer);
  }, [api]);
  const alive = useRef(true);
  const localAutoAuth = api instanceof HttpApi && api.localAutoAuth;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  async function load(next?: string) {
    setLoading(true);
    setError("");
    try {
      const page = await api.products(next);
      if (!alive.current) return;
      setProducts((list) =>
        next
          ? [
              ...list,
              ...page.items.filter((p) => !list.some((x) => x.id === p.id)),
            ]
          : page.items,
      );
      setCursor(page.next_cursor);
      if (!next) setSelected(page.items[0] || null);
    } catch (e) {
      if (alive.current) setError(errorMessage(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [api]);
  const filtered = products.filter((p) =>
    (category === "all" || p.product_type === category) && `${p.title} ${p.product_type || ""} ${p.id}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <>
      {(api.demo || connectionNotice) && <div role="status" className={"environment-banner " + (!api.demo ? "live" : "")}>
        {api.demo
          ? "SAMPLE MODE · Illustrative products and results. Nothing is sent to a server."
          : "CONNECTED · Live workspace data. Actions are authorized by the server."}
      </div>}
      <div className="app-shell">
        <aside className="sidebar">
          <a
            className="brand"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              setView("pm");
            }}
          >
            <span className="brand-mark">
              <i />
              <i />
              <i />
            </span>
            <span className="brand-wordmark"><strong>PFIA</strong><small>Product Feedback<br />Intelligence Agent</small></span>
          </a>
          <nav aria-label="Workspace">
            <button
              className={view === "pm" ? "active" : ""}
              onClick={() => setView("pm")}
            >
              <LayoutGrid size={17} />
              Customer feedback
            </button>
            <button
              className={view === "reviewer" ? "active" : ""}
              onClick={() => setView("reviewer")}
            >
              <MessageSquare size={17} />
              Write a review
            </button>
            {!api.publicDemo && <button className={view === "legacy" ? "active" : ""} onClick={() => setView("legacy")}>
              <LayoutGrid size={17} /> Legacy analysis
            </button>}
          </nav>
          <div className="sidebar-bottom">
            <span className="connection-dot" />
            {api.demo ? "Sample workspace" : "Connected workspace"}
          </div>
        </aside>
        <main>
          <header className="topbar">
            <div className="workspace-heading"><span className="top-label">Product Feedback Intelligence Agent</span><strong>{view === "pm" ? "Customer intelligence" : view === "reviewer" ? "Share your experience" : "Review analysis"}</strong></div>
            <div className="top-actions">
              {!localAutoAuth && !api.publicDemo && <button className="text-button" onClick={onConnect}>
                <Plug size={14} />
                {api.demo ? "Connect API" : "Change account"}
              </button>}
              {!api.demo && !localAutoAuth && !api.publicDemo && (
                <button
                  className="icon-button"
                  aria-label="Disconnect"
                  onClick={onDisconnect}
                >
                  <LogOut size={16} />
                </button>
              )}
              <button
                className="button"
                onClick={() => setView(view === "pm" ? "reviewer" : "pm")}
              >
                {view === "pm" ? "Write a review" : "Back to feedback"}
                <ArrowRight size={14} />
              </button>
            </div>
          </header>
          <div className="content">
            <section className="product-selection" aria-label="Selected product">
              <div className="selection-heading"><div><span className="eyebrow">Your product catalog</span><h2>Explore customer feedback</h2></div><button className="button primary" onClick={() => setPicker(true)} disabled={!products.length}>Browse products <ChevronDown size={15}/></button></div>
              <div className="product-button selected-product-details">
                {selected && <ProductImage product={selected}/>}
                <span className="selected-product-copy"><small>{selected?.product_type || "Selected product"}</small><h1>{selected?.title || "Choose a product"}</h1><span>Product ID {selected?.id || "—"}</span></span>
              </div>
            </section>
            {error && <ErrorNotice message={error} retry={() => load()} />}{" "}
            {!selected ? (
              loading ? (
                <Loading>Loading products…</Loading>
              ) : (
                !error && (
                  <section className="empty-state">
                    <h2>No products yet.</h2>
                    <p>Ask the demo host to load a product catalog.</p>
                  </section>
                )
              )
            ) : (
              <div key={selected.id}>
                <div hidden={view !== "pm"}>
                  <SummaryDashboard
                    api={api}
                    product={selected}
                    reviewVersion={reviewVersion}
                    active={view === "pm"}
                  />
                </div>
                {view === "legacy" && <Dashboard api={api} product={selected} reviewVersion={reviewVersion} active />}
                <div hidden={view !== "reviewer"}>
                  <Reviewer
                    api={api}
                    product={selected}
                    onSaved={() => setReviewVersion((n) => n + 1)}
                    active={view === "reviewer"}
                  />
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
      {picker && (
        <Modal title="Choose a product" onClose={() => setPicker(false)} wide>
          <label className="search">
            <Search size={17} />
            <input
              aria-label="Search loaded products"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search loaded products or categories"
            />
          </label>
          <div className="catalog-categories" aria-label="Product categories"><button className={category === "all" ? "active" : ""} onClick={() => setCategory("all")}>All products</button>{[...new Set(products.map(p=>p.product_type).filter((c): c is string => !!c))].sort().map(c=><button key={c} className={category === c ? "active" : ""} onClick={()=>setCategory(c)}>{c}</button>)}</div>
          <p className="fine-print">
            {products.length} products loaded
            {cursor ? " · Load more to expand your search." : "."}
          </p>
          <div className="catalog">
            {filtered.map((p) => (
              <button
                key={p.id}
                className={
                  "catalog-item " + (selected?.id === p.id ? "selected" : "")
                }
                onClick={() => {
                  setSelected(p);
                  setReviewVersion(0);
                  setPicker(false);
                }}
              >
                <ProductImage product={p}/>
                <span className="category">{p.product_type || "Product"}</span>
                <strong>{p.title}</strong>
                <small>{p.id}</small>
              </button>
            ))}
            {!filtered.length && (
              <p className="muted">No match in the products loaded so far.</p>
            )}
          </div>
          {error && <ErrorNotice message={error} />}{" "}
          {cursor && (
            <button
              className="button full"
              disabled={loading}
              onClick={() => load(cursor)}
            >
              {loading ? "Loading…" : "Load more products"}
            </button>
          )}
        </Modal>
      )}
    </>
  );
}
export default function App() {
  const [api, setApi] = useState<Api>(() => new DemoApi()),
    [session, setSession] = useState(0),
    [connection, setConnection] = useState(false),
    [bootstrap, setBootstrap] = useState<"checking" | "ready" | "error">("checking"),
    [bootstrapError, setBootstrapError] = useState(""),
    [bootstrapAttempt, setBootstrapAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setBootstrap("checking"); setBootstrapError("");
    async function start() {
      try {
        if (!import.meta.env.DEV) {
          await waitForBackend(controller.signal);
          const token = await openDemoSession(controller.signal);
          if (!cancelled) { setApi(new HttpApi(token, fetch, false, true)); setSession((n) => n + 1); setBootstrap("ready"); }
        } else {
        const response = await fetch("/__local_demo_auth", { cache: "no-store" });
        if (!response.ok) throw new Error("Local development auth is unavailable.");
        const state = await response.json() as { enabled?: boolean; error?: string | null };
        if (state.error) throw new Error(state.error);
        if (state.enabled) {
          const local = new HttpApi("", fetch, true);
          await waitForBackend(controller.signal);
          if (!cancelled) { setApi(local); setSession((n) => n + 1); }
        }
        if (!cancelled) setBootstrap("ready");
        }
      } catch (error) {
        if (!cancelled) {
          setBootstrapError(error instanceof Error ? error.message : "Local API connection failed.");
          setBootstrap("error");
        }
      }
    }
    void start();
    return () => { cancelled = true; controller.abort(); };
  }, [bootstrapAttempt]);
  function connect(next: Api) {
    setApi(next);
    setSession((n) => n + 1);
    setConnection(false);
  }
  return (
    <>
      {bootstrap === "checking" && <main className="startup-screen"><div className="startup-card"><span className="startup-brand">PFIA</span><h1>Product Feedback Intelligence Agent</h1><p>Bringing your product insights into focus.</p><Loading>Connecting to your workspace…</Loading></div></main>}
      {bootstrap === "error" && <div className="content"><ErrorNotice message={bootstrapError} retry={() => setBootstrapAttempt((n) => n + 1)} /></div>}
      {bootstrap === "ready" && <>
      <Workspace
        key={session}
        api={api}
        onConnect={() => setConnection(true)}
        onDisconnect={() => connect(new DemoApi())}
      />
      {connection && (
        <Connection onClose={() => setConnection(false)} onConnect={connect} />
      )}
      </>}
    </>
  );
}
