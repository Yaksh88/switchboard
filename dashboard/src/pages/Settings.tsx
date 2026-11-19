import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { getToken, setToken } from "../api/client";

export function Settings() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [token, setValue] = useState(getToken);
  const unauthorized = params.get("reason") === "unauthorized";

  const save = (event: FormEvent) => {
    event.preventDefault();
    setToken(token.trim());
    void queryClient.invalidateQueries();
    navigate("/");
  };

  const clear = () => {
    setToken("");
    setValue("");
    void queryClient.clear();
  };

  return (
    <div className="page">
      <header className="page__head">
        <div>
          <h1>Settings</h1>
          <p className="page__lede">The dashboard reads from the gateway's admin API.</p>
        </div>
      </header>

      {unauthorized ? (
        <div className="notice notice--fault" role="alert">
          <p className="notice__title">The gateway rejected this token</p>
          <p className="notice__body">
            Enter the value of SWITCHBOARD_ADMIN_TOKEN from the gateway's environment.
          </p>
        </div>
      ) : null}

      <section className="section">
        <h2>Admin token</h2>
        <form onSubmit={save} className="filters" style={{ alignItems: "flex-end" }}>
          <label className="field" style={{ flex: "1 1 320px", maxWidth: 480 }}>
            Token
            <input
              className="input"
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Paste the admin token"
            />
          </label>
          <button type="submit" className="button" disabled={!token.trim()}>
            Save token
          </button>
          <button type="button" className="button button--quiet" onClick={clear}>
            Remove token
          </button>
        </form>
        <p className="muted" style={{ maxWidth: "70ch" }}>
          The token is kept in this browser's local storage and sent only to the gateway that serves
          this page.
        </p>
      </section>
    </div>
  );
}
