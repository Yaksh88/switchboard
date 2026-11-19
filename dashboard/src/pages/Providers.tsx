import { useMemo } from "react";

import { useProviders } from "../api/hooks";
import type { BreakerState, ProviderHealth } from "../api/types";
import { Empty, ErrorNotice, Loading } from "../components/parts";

const STATE_LABEL: Record<BreakerState, string> = {
  closed: "Carrying traffic",
  half_open: "Testing one request",
  open: "Skipped",
};

function Jack({ target }: { target: ProviderHealth }) {
  return (
    <div className={`jack jack--${target.state}`}>
      <span className="jack__lamp" aria-hidden="true" />
      <span className="jack__socket" aria-hidden="true">
        <span className="jack__hole" />
      </span>
      <span className="jack__model">{target.model}</span>
      <span className="jack__state">{STATE_LABEL[target.state]}</span>
    </div>
  );
}

export function Providers() {
  const providers = useProviders();

  const lines = useMemo(() => {
    const grouped = new Map<string, ProviderHealth[]>();
    for (const target of providers.data ?? []) {
      grouped.set(target.provider, [...(grouped.get(target.provider) ?? []), target]);
    }
    return [...grouped.entries()];
  }, [providers.data]);

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1>Providers</h1>
          <p className="page__lede">
            Each jack is one upstream model the gateway can route to. The panel refreshes every five
            seconds.
          </p>
        </div>
      </header>

      {providers.isError ? <ErrorNotice error={providers.error} /> : null}
      {providers.isPending ? <Loading what="providers" /> : null}

      {providers.data && lines.length === 0 ? (
        <Empty title="No providers configured">
          <p>Add providers and models to the gateway config, then restart it.</p>
        </Empty>
      ) : null}

      {lines.length > 0 ? (
        <>
          <section className="board" aria-label="Provider patch panel">
            {lines.map(([provider, targets]) => (
              <div className="line" key={provider}>
                <h2 className="line__name">{provider}</h2>
                <div className="line__jacks">
                  {targets.map((target) => (
                    <Jack key={`${target.provider}/${target.model}`} target={target} />
                  ))}
                </div>
              </div>
            ))}
          </section>
          <p className="muted" style={{ maxWidth: "70ch" }}>
            A lit jack is carrying traffic. After five failures in a row the gateway stops sending to
            a model for 30 seconds, then lets a single request through to test it. If that request
            succeeds the jack lights again.
          </p>
        </>
      ) : null}
    </div>
  );
}
