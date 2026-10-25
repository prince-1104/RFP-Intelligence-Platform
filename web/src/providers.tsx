import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api/client";
import type { CollectionRow, Health } from "./api/types";

type Selection = {
  collection: string;
  bid: string;
  collections: CollectionRow[];
  health: Health | null;
  healthLabel: string;
  setCollection: (value: string) => void;
  setBid: (value: string | ((current: string) => string)) => void;
  toast: string;
  notify: (text: string) => void;
  clearToast: () => void;
};

const SelectionContext = createContext<Selection | null>(null);

export function Providers({ children }: { children: ReactNode }) {
  const [collection, setCollection] = useState("");
  const [bid, setBid] = useState("");
  const [collections, setCollections] = useState<CollectionRow[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthLabel, setHealthLabel] = useState("Unavailable");
  const [toast, setToast] = useState("");

  useEffect(() => {
    let active = true;
    api.health().then((body) => {
      if (!active) return;
      setHealth(body);
      setHealthLabel(body.qdrant === "healthy" ? "Qdrant healthy" : "Unavailable");
    }).catch(() => {
      if (active) setHealthLabel("Unavailable");
    });
    api.collections().then((rows) => {
      if (!active) return;
      setCollections(rows);
      setCollection((current) => current || rows[0]?.base || "");
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!collection) return;
    let active = true;
    api.bids(collection).then((body) => {
      if (!active) return;
      setBid((current) => (
        body.bids.some((row) => row.label === current) ? current : (body.bids[0]?.label ?? "")
      ));
    }).catch(() => undefined);
    return () => { active = false; };
  }, [collection]);

  const value = useMemo<Selection>(() => ({
    collection, bid, collections, health, healthLabel, setCollection, setBid,
    toast, notify: setToast, clearToast: () => setToast(""),
  }), [collection, bid, collections, health, healthLabel, toast]);

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function useSelection(): Selection {
  const value = useContext(SelectionContext);
  if (!value) throw new Error("Selection is missing");
  return value;
}
