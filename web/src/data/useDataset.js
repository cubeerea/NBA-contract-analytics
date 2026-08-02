import { useCallback, useEffect, useMemo, useState } from 'react';
import { boundsOf, contractTypesInData, teamsInData } from '../model/valuation.js';

/** Where the built site looks for the ETL artifacts. */
const DATA_BASE = `${import.meta.env.BASE_URL ?? '/'}data`.replace(/\/{2,}/g, '/');

const FILES = ['players.json', 'meta.json', 'teams.json'];

/**
 * Required columns. Validated on load so a schema drift on the ETL side
 * surfaces as a legible error in the UI rather than a wall of NaNs.
 * Mirrors PLAYER_RECORD_SCHEMA in etl/schema.py.
 */
const REQUIRED_PLAYER_FIELDS = [
  'bbref_slug',
  'name',
  'headshot_url',
  'team',
  'position',
  'age',
  'composite_score',
  'cap_pct',
  'salary',
  'war_blended',
  'war_spread',
  'surplus_naive',
  'surplus_replacement',
  'expected_salary_naive',
  'expected_salary_replacement',
  'contract_type'
];

async function getJSON(file, signal) {
  const res = await fetch(`${DATA_BASE}/${file}`, { signal, cache: 'no-cache' });
  if (!res.ok) throw new Error(`${file}: HTTP ${res.status} ${res.statusText}`);
  try {
    return await res.json();
  } catch {
    throw new Error(`${file}: response was not valid JSON`);
  }
}

function validate(players, meta) {
  if (!Array.isArray(players)) throw new Error('players.json must be an array of player records');
  if (!players.length) return { warnings: [] };
  const sample = players[0];
  const missing = REQUIRED_PLAYER_FIELDS.filter((f) => !(f in sample));
  if (missing.length) {
    throw new Error(
      `players.json is missing required field${missing.length > 1 ? 's' : ''}: ${missing.join(', ')}`
    );
  }
  const warnings = [];
  if (!meta?.regression?.replacement || !meta?.regression?.naive) {
    warnings.push('meta.regression is missing a naive or replacement fit; the league line is hidden.');
  }
  if (!meta?.salary_cap) warnings.push('meta.salary_cap is missing.');
  return { warnings };
}

export function useDataset() {
  const [state, setState] = useState({ status: 'loading', error: null, data: null, warnings: [] });
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    const ctrl = new AbortController();
    let live = true;
    setState((s) => ({ ...s, status: 'loading', error: null }));

    Promise.all(FILES.map((f) => getJSON(f, ctrl.signal)))
      .then(([players, meta, teams]) => {
        if (!live) return;
        const { warnings } = validate(players, meta);
        setState({
          status: players.length ? 'ready' : 'empty',
          error: null,
          warnings,
          data: { players, meta: meta ?? {}, teams: Array.isArray(teams) ? teams : [] }
        });
      })
      .catch((err) => {
        if (!live || err.name === 'AbortError') return;
        setState({ status: 'error', error: err, data: null, warnings: [] });
      });

    return () => {
      live = false;
      ctrl.abort();
    };
  }, [nonce]);

  const derived = useMemo(() => {
    const players = state.data?.players ?? [];
    if (!players.length) return { bounds: null, teamList: [], contractTypeList: [], byTeam: new Map() };
    const byTeam = new Map((state.data?.teams ?? []).map((t) => [t.team, t]));
    return {
      bounds: boundsOf(players),
      teamList: teamsInData(players),
      contractTypeList: contractTypesInData(players),
      byTeam
    };
  }, [state.data]);

  return { ...state, ...derived, reload, dataBase: DATA_BASE };
}
