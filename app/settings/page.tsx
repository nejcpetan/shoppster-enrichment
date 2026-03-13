"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { fetchAPI } from "@/lib/api";
import { AuthGuard } from "@/components/AuthGuard";
import { UserMenu } from "@/components/UserMenu";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  ArrowLeft, Search, GitBranch, CheckSquare, Globe, DollarSign,
  Cpu, Tag, Download, Save, RefreshCw, X, Plus, RotateCcw,
  AlertCircle, CheckCircle2, Settings,
} from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface CompanyConfig {
  company_name: string;
  company_slug: string;
  source: {
    strategy: string;
    search_provider: string;
    max_manufacturer_results: number;
    max_general_results: number;
    max_general_queries: number;
    max_total_results: number;
    allowed_domains: string[];
    blocked_domains: string[];
    trust_third_party_as_primary: boolean;
  };
  pipeline: {
    enable_ean_lookup: boolean;
    enable_gap_fill: boolean;
    enable_gemini_vision: boolean;
    max_pages_to_scrape: number;
    max_gap_fill_pages: number;
    gap_fill_is_primary: boolean;
  };
  critical_fields: {
    net_weight: boolean;
    packaged_weight: boolean;
    packaged_dims: boolean;
    warranty: boolean;
    short_description: boolean;
    color: boolean;
    country_of_origin: boolean;
  };
  language: {
    primary_languages: string[];
    output_language: string;
    extra_color_mappings: Record<string, string>;
    extra_country_mappings: Record<string, string>;
  };
  cost: {
    max_daily_cost_usd: number;
    daily_product_limit: number;
    max_batch_size: number;
    market_region: string;
  };
  llm: {
    triage_model: string;
    search_model: string;
    extract_model: string;
    gap_fill_model: string;
    validate_model: string;
  };
  brands: {
    known_brands: string[];
    brand_coo_seeds: Record<string, string>;
  };
  export: {
    include_cost_data: boolean;
    include_enrichment_log: boolean;
    column_overrides: Record<string, string>;
  };
}

// ─── Sections ─────────────────────────────────────────────────────────────────

const SECTIONS = [
  { id: "source",          label: "Data Sources",    icon: Search,      description: "Where and how to find product data" },
  { id: "pipeline",        label: "Pipeline",        icon: GitBranch,   description: "Feature flags and scrape limits" },
  { id: "critical_fields", label: "Critical Fields", icon: CheckSquare, description: "Which missing fields trigger gap-fill" },
  { id: "language",        label: "Language",        icon: Globe,       description: "Localization and output language" },
  { id: "cost",            label: "Cost & Limits",   icon: DollarSign,  description: "Daily budget guardrails" },
  { id: "llm",             label: "LLM Models",      icon: Cpu,         description: "AI model selection per pipeline phase" },
  { id: "brands",          label: "Brands",          icon: Tag,         description: "Known brands and country-of-origin hints" },
  { id: "export",          label: "Export",          icon: Download,    description: "Output format and column options" },
];

// ─── Main page ────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [config, setConfig] = useState<CompanyConfig | null>(null);
  const [overrides, setOverrides] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<Record<string, any>>({});
  const [activeSection, setActiveSection] = useState("source");
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error">("idle");
  const [loading, setLoading] = useState(true);

  const loadSettings = useCallback(async () => {
    try {
      const data = await fetchAPI("/settings");
      setConfig(data.config);
      setOverrides(new Set(data.overrides));
    } catch (e) {
      console.error("Failed to load settings", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadSettings(); }, [loadSettings]);

  function getField(key: string): any {
    if (key in pending) return pending[key];
    if (!config) return undefined;
    const parts = key.split(".");
    let val: any = config;
    for (const p of parts) val = val?.[p];
    return val;
  }

  function setField(key: string, value: any) {
    if (!config) return;
    const parts = key.split(".");
    let original: any = config;
    for (const p of parts) original = original?.[p];
    if (JSON.stringify(value) === JSON.stringify(original)) {
      setPending((prev) => { const next = { ...prev }; delete next[key]; return next; });
    } else {
      setPending((prev) => ({ ...prev, [key]: value }));
    }
  }

  async function saveAll() {
    const changes = Object.entries(pending);
    if (!changes.length) return;
    setSaving(true);
    setSaveStatus("idle");
    try {
      for (const [key, value] of changes) {
        await fetchAPI("/settings", {
          method: "PUT",
          body: JSON.stringify({ key, value: JSON.stringify(value) }),
        });
      }
      await loadSettings();
      setPending({});
      setSaveStatus("saved");
      setTimeout(() => setSaveStatus("idle"), 3000);
    } catch {
      setSaveStatus("error");
    } finally {
      setSaving(false);
    }
  }

  async function resetField(key: string) {
    try {
      await fetchAPI(`/settings/${key}`, { method: "DELETE" });
      await loadSettings();
      setPending((prev) => { const next = { ...prev }; delete next[key]; return next; });
    } catch (e) {
      console.error("Reset failed", e);
    }
  }

  function discardAll() {
    setPending({});
  }

  const totalPendingCount = Object.keys(pending).length;
  const sectionHasPending = (id: string) => Object.keys(pending).some((k) => k.startsWith(id + "."));
  const sectionHasOverrides = (id: string) => [...overrides].some((k) => k.startsWith(id + "."));

  if (loading || !config) {
    return (
      <AuthGuard>
        <div className="h-screen flex items-center justify-center">
          <RefreshCw className="w-6 h-6 animate-spin text-muted-foreground" />
        </div>
      </AuthGuard>
    );
  }

  const ctx: FieldCtx = { getField, setField, overrides, resetField };

  return (
    <AuthGuard>
      <div className="min-h-screen">

        {/* Nav */}
        <nav className="border-b sticky top-0 z-50 bg-background/80 backdrop-blur-md">
          <div className="max-w-[1400px] mx-auto px-6 h-16 flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Link href="/">
                <Button variant="ghost" size="sm">
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  Back to Products
                </Button>
              </Link>
              <Separator orientation="vertical" className="h-6" />
              <div className="flex items-center gap-2">
                <Settings className="w-5 h-5 text-muted-foreground" />
                <h1 className="font-bold text-lg tracking-tight">Settings</h1>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted-foreground font-mono">{config.company_slug}</span>
              <UserMenu />
            </div>
          </div>
        </nav>

        {/* Body */}
        <div className="max-w-[1400px] mx-auto px-6 py-8 flex gap-8 items-start">

          {/* Sidebar */}
          <aside className="w-52 shrink-0 sticky top-24">
            <nav className="space-y-0.5">
              {SECTIONS.map((section) => {
                const Icon = section.icon;
                const hasPending = sectionHasPending(section.id);
                const hasOverrides = sectionHasOverrides(section.id);
                const active = activeSection === section.id;
                return (
                  <button
                    key={section.id}
                    onClick={() => setActiveSection(section.id)}
                    className={cn(
                      "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all text-left",
                      active
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                    )}
                  >
                    <Icon className={cn("w-4 h-4 shrink-0", active && "text-indigo-400")} />
                    <span className="flex-1 font-medium">{section.label}</span>
                    {hasPending && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />}
                    {!hasPending && hasOverrides && <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 shrink-0" />}
                  </button>
                );
              })}
            </nav>
            <Separator className="my-4" />
            <div className="px-3 space-y-2">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
                Unsaved changes
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="w-1.5 h-1.5 rounded-full bg-indigo-500 shrink-0" />
                DB override active
              </div>
            </div>
          </aside>

          {/* Content */}
          <main className="flex-1 min-w-0 space-y-4 pb-32">
            {(() => {
              const s = SECTIONS.find((x) => x.id === activeSection)!;
              const Icon = s.icon;
              return (
                <div className="mb-6">
                  <div className="flex items-center gap-2.5 mb-1">
                    <Icon className="w-5 h-5 text-indigo-400" />
                    <h2 className="text-xl font-bold">{s.label}</h2>
                  </div>
                  <p className="text-sm text-muted-foreground">{s.description}</p>
                </div>
              );
            })()}

            {activeSection === "source"          && <SourceSection          ctx={ctx} />}
            {activeSection === "pipeline"        && <PipelineSection        ctx={ctx} />}
            {activeSection === "critical_fields" && <CriticalFieldsSection  ctx={ctx} />}
            {activeSection === "language"        && <LanguageSection        ctx={ctx} />}
            {activeSection === "cost"            && <CostSection            ctx={ctx} />}
            {activeSection === "llm"             && <LLMSection             ctx={ctx} />}
            {activeSection === "brands"          && <BrandsSection          ctx={ctx} />}
            {activeSection === "export"          && <ExportSection          ctx={ctx} />}
          </main>
        </div>

        {/* Floating save bar */}
        {totalPendingCount > 0 && (
          <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
            <Card className="flex-row items-center gap-4 px-6 py-3.5 shadow-2xl border-border">
              <span className="text-sm text-muted-foreground">
                {totalPendingCount} unsaved change{totalPendingCount !== 1 ? "s" : ""}
              </span>
              <Separator orientation="vertical" className="h-4" />
              {saveStatus === "saved" && (
                <span className="text-xs text-emerald-400 flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Saved
                </span>
              )}
              {saveStatus === "error" && (
                <span className="text-xs text-red-400 flex items-center gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5" /> Error saving
                </span>
              )}
              <Button variant="ghost" size="sm" onClick={discardAll}>
                Discard
              </Button>
              <Button size="sm" onClick={saveAll} disabled={saving}>
                {saving
                  ? <><RefreshCw className="w-4 h-4 mr-2 animate-spin" />Saving…</>
                  : <><Save className="w-4 h-4 mr-2" />Save Changes</>}
              </Button>
            </Card>
          </div>
        )}
      </div>
    </AuthGuard>
  );
}

// ─── Field context ────────────────────────────────────────────────────────────

interface FieldCtx {
  getField: (key: string) => any;
  setField: (key: string, value: any) => void;
  overrides: Set<string>;
  resetField: (key: string) => void;
}

// ─── Shared atoms ─────────────────────────────────────────────────────────────

function OverrideBadge() {
  return (
    <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4 font-medium text-indigo-400">
      DB override
    </Badge>
  );
}

function ResetButton({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" onClick={onClick}
      className="h-auto p-0 text-xs text-muted-foreground hover:text-foreground">
      <RotateCcw className="w-3 h-3 mr-1" />
      Reset to default
    </Button>
  );
}

function FieldRow({
  label, description, dotKey, overrides, onReset, children,
}: {
  label: string;
  description?: string;
  dotKey: string;
  overrides: Set<string>;
  onReset: (k: string) => void;
  children: React.ReactNode;
}) {
  const isOverridden = overrides.has(dotKey);
  return (
    <div className="flex items-center justify-between gap-6 py-4">
      <div className="space-y-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <Label>{label}</Label>
          {isOverridden && <OverrideBadge />}
        </div>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        {isOverridden && <ResetButton onClick={() => onReset(dotKey)} />}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function WideField({
  label, description, dotKey, overrides, onReset, children,
}: {
  label: string; description?: string; dotKey: string;
  overrides: Set<string>; onReset: (k: string) => void;
  children: React.ReactNode;
}) {
  const isOverridden = overrides.has(dotKey);
  return (
    <div className="py-4 space-y-3">
      <div className="space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <Label>{label}</Label>
          {isOverridden && <OverrideBadge />}
        </div>
        {description && <p className="text-xs text-muted-foreground">{description}</p>}
        {isOverridden && <ResetButton onClick={() => onReset(dotKey)} />}
      </div>
      {children}
    </div>
  );
}

// ─── Input wrappers ───────────────────────────────────────────────────────────

function SettingsSelect({
  value, onChange, options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-[210px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function NumberField({
  value, onChange, min, max, step = 1, prefix,
}: {
  value: number; onChange: (v: number) => void;
  min?: number; max?: number; step?: number; prefix?: string;
}) {
  return (
    <div className="relative">
      {prefix && (
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground text-sm pointer-events-none z-10">
          {prefix}
        </span>
      )}
      <Input
        type="number" value={value}
        onChange={(e) => {
          const v = step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value, 10);
          if (!isNaN(v)) onChange(v);
        }}
        min={min} max={max} step={step}
        className={cn("w-28 font-mono", prefix && "pl-7")}
      />
    </div>
  );
}

function TagInput({
  value, onChange, placeholder = "Add…",
}: {
  value: string[]; onChange: (v: string[]) => void; placeholder?: string;
}) {
  const [input, setInput] = useState("");
  const add = () => {
    const t = input.trim();
    if (t && !value.includes(t)) { onChange([...value, t]); setInput(""); }
  };
  return (
    <div className="space-y-2 w-full">
      <div className="flex flex-wrap gap-1.5 min-h-[38px] p-2 border rounded-md bg-muted/50">
        {value.length === 0 && <span className="text-xs text-muted-foreground self-center px-1">None</span>}
        {value.map((tag) => (
          <Badge key={tag} variant="secondary" className="gap-1 text-xs">
            {tag}
            <button onClick={() => onChange(value.filter((t) => t !== tag))}
              className="text-muted-foreground hover:text-foreground transition-colors">
              <X className="w-3 h-3" />
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex gap-2">
        <Input value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
          placeholder={placeholder} />
        <Button size="sm" variant="outline" onClick={add} className="px-3">
          <Plus className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}

function KVInput({
  value, onChange, keyPlaceholder = "key", valuePlaceholder = "value",
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  const add = () => {
    if (newKey.trim()) {
      onChange({ ...value, [newKey.trim()]: newVal.trim() });
      setNewKey(""); setNewVal("");
    }
  };
  return (
    <div className="space-y-2 w-full">
      {Object.entries(value).length === 0 && (
        <p className="text-xs text-muted-foreground px-1">No entries yet</p>
      )}
      {Object.entries(value).map(([k, v]) => (
        <div key={k} className="flex items-center gap-2 border rounded-md px-3 py-2">
          <span className="font-mono text-xs flex-1 truncate">{k}</span>
          <span className="text-muted-foreground text-xs shrink-0">→</span>
          <span className="font-mono text-xs flex-1 truncate">{v}</span>
          <button onClick={() => { const next = { ...value }; delete next[k]; onChange(next); }}
            className="text-muted-foreground hover:text-destructive transition-colors shrink-0 ml-1">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
      <div className="flex gap-2">
        <Input value={newKey} onChange={(e) => setNewKey(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
          placeholder={keyPlaceholder} className="font-mono text-xs w-32" />
        <span className="text-muted-foreground self-center text-xs">→</span>
        <Input value={newVal} onChange={(e) => setNewVal(e.target.value)}
          placeholder={valuePlaceholder} className="flex-1 font-mono text-xs" />
        <Button size="sm" variant="outline" onClick={add} className="px-3">
          <Plus className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}

// ─── Section components ───────────────────────────────────────────────────────

function SourceSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Search Strategy</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <FieldRow label="Source Strategy" description="How the pipeline prioritises which sources to trust." {...fp("source.strategy")}>
            <SettingsSelect value={g("source.strategy")} onChange={(v) => s("source.strategy", v)}
              options={[
                { value: "official_first", label: "Official First" },
                { value: "any_source",     label: "Any Source" },
                { value: "official_only",  label: "Official Only" },
              ]} />
          </FieldRow>
          <FieldRow label="Search Provider" description="Third-party search API used for web queries." {...fp("source.search_provider")}>
            <SettingsSelect value={g("source.search_provider")} onChange={(v) => s("source.search_provider", v)}
              options={[
                { value: "tavily",    label: "Tavily" },
                { value: "firecrawl", label: "Firecrawl" },
              ]} />
          </FieldRow>
          <FieldRow label="Trust Third-Party as Primary" description="Treat third-party sites with the same weight as official sources." {...fp("source.trust_third_party_as_primary")}>
            <Switch checked={g("source.trust_third_party_as_primary")} onCheckedChange={(v) => s("source.trust_third_party_as_primary", v)} />
          </FieldRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Result Limits</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <FieldRow label="Max Manufacturer Results" description="Max results fetched from the manufacturer domain." {...fp("source.max_manufacturer_results")}>
            <NumberField value={g("source.max_manufacturer_results")} onChange={(v) => s("source.max_manufacturer_results", v)} min={1} max={20} />
          </FieldRow>
          <FieldRow label="Max General Results" description="Max results from general web search." {...fp("source.max_general_results")}>
            <NumberField value={g("source.max_general_results")} onChange={(v) => s("source.max_general_results", v)} min={1} max={20} />
          </FieldRow>
          <FieldRow label="Max General Queries" description="Number of search queries issued per product." {...fp("source.max_general_queries")}>
            <NumberField value={g("source.max_general_queries")} onChange={(v) => s("source.max_general_queries", v)} min={1} max={10} />
          </FieldRow>
          <FieldRow label="Max Total Results" description="Stop collecting results after this many, regardless of source." {...fp("source.max_total_results")}>
            <NumberField value={g("source.max_total_results")} onChange={(v) => s("source.max_total_results", v)} min={1} max={30} />
          </FieldRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Domain Filters</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <WideField label="Allowed Domains" description="If non-empty, only search these domains (whitelist mode)." {...fp("source.allowed_domains")}>
            <TagInput value={g("source.allowed_domains") ?? []} onChange={(v) => s("source.allowed_domains", v)} placeholder="example.com" />
          </WideField>
          <WideField label="Blocked Domains" description="Never include results from these domains." {...fp("source.blocked_domains")}>
            <TagInput value={g("source.blocked_domains") ?? []} onChange={(v) => s("source.blocked_domains", v)} placeholder="spamsite.com" />
          </WideField>
        </CardContent>
      </Card>
    </div>
  );
}

function PipelineSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Feature Flags</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <FieldRow label="EAN Lookup" description="Resolve brand/category from EAN barcode before enrichment." {...fp("pipeline.enable_ean_lookup")}>
            <Switch checked={g("pipeline.enable_ean_lookup")} onCheckedChange={(v) => s("pipeline.enable_ean_lookup", v)} />
          </FieldRow>
          <FieldRow label="Gap Fill" description="Run a second-pass search if critical fields are still missing after extraction." {...fp("pipeline.enable_gap_fill")}>
            <Switch checked={g("pipeline.enable_gap_fill")} onCheckedChange={(v) => s("pipeline.enable_gap_fill", v)} />
          </FieldRow>
          <FieldRow label="Gemini Vision" description="Use Gemini to detect product color from images." {...fp("pipeline.enable_gemini_vision")}>
            <Switch checked={g("pipeline.enable_gemini_vision")} onCheckedChange={(v) => s("pipeline.enable_gemini_vision", v)} />
          </FieldRow>
          <FieldRow label="Gap Fill is Primary" description="Run gap-fill with higher priority — useful when data is mainly on third-party sites." {...fp("pipeline.gap_fill_is_primary")}>
            <Switch checked={g("pipeline.gap_fill_is_primary")} onCheckedChange={(v) => s("pipeline.gap_fill_is_primary", v)} />
          </FieldRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Scrape Limits</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <FieldRow label="Max Pages to Scrape" description="Max pages sent to LLM extraction (manufacturer + authorized sources)." {...fp("pipeline.max_pages_to_scrape")}>
            <NumberField value={g("pipeline.max_pages_to_scrape")} onChange={(v) => s("pipeline.max_pages_to_scrape", v)} min={1} max={20} />
          </FieldRow>
          <FieldRow label="Max Gap-Fill Pages" description="Max third-party pages checked during the gap-fill pass." {...fp("pipeline.max_gap_fill_pages")}>
            <NumberField value={g("pipeline.max_gap_fill_pages")} onChange={(v) => s("pipeline.max_gap_fill_pages", v)} min={1} max={10} />
          </FieldRow>
        </CardContent>
      </Card>
    </div>
  );
}

function CriticalFieldsSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  const fields = [
    { key: "critical_fields.net_weight",        label: "Net Weight",          description: "Product weight without packaging" },
    { key: "critical_fields.packaged_weight",   label: "Packaged Weight",     description: "Total weight including packaging" },
    { key: "critical_fields.packaged_dims",     label: "Packaged Dimensions", description: "Box dimensions for logistics (H×W×D)" },
    { key: "critical_fields.warranty",          label: "Warranty",            description: "Warranty duration and type" },
    { key: "critical_fields.short_description", label: "Short Description",   description: "1–2 sentence product summary" },
    { key: "critical_fields.color",             label: "Color",               description: "Product color" },
    { key: "critical_fields.country_of_origin", label: "Country of Origin",   description: "Where the product was manufactured" },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Gap-Fill Triggers</CardTitle>
        <CardDescription>When a critical field is missing after main extraction, the pipeline runs a targeted gap-fill pass.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-0 divide-y">
        {fields.map(({ key, label, description }) => (
          <FieldRow key={key} label={label} description={description} {...fp(key)}>
            <Switch checked={g(key)} onCheckedChange={(v) => s(key, v)} />
          </FieldRow>
        ))}
      </CardContent>
    </Card>
  );
}

function LanguageSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Language Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <FieldRow label="Output Language" description="Language code for generated descriptions and features (e.g. en, sl, de)." {...fp("language.output_language")}>
            <Input value={g("language.output_language")} onChange={(e) => s("language.output_language", e.target.value)} placeholder="en" className="w-24" />
          </FieldRow>
          <WideField label="Primary Languages" description="Languages to look for in product data. First in the list is primary." {...fp("language.primary_languages")}>
            <TagInput value={g("language.primary_languages") ?? []} onChange={(v) => s("language.primary_languages", v)} placeholder="sl" />
          </WideField>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Custom Mappings</CardTitle>
          <CardDescription>Override how color or country names are normalised. Merged with platform defaults.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <WideField label="Color Mappings" description="Map native-language color names to English — e.g. türkis → turquoise" {...fp("language.extra_color_mappings")}>
            <KVInput value={g("language.extra_color_mappings") ?? {}} onChange={(v) => s("language.extra_color_mappings", v)} keyPlaceholder="native name" valuePlaceholder="english name" />
          </WideField>
          <WideField label="Country Mappings" description="Map native-language country names to English — e.g. Kitajska → China" {...fp("language.extra_country_mappings")}>
            <KVInput value={g("language.extra_country_mappings") ?? {}} onChange={(v) => s("language.extra_country_mappings", v)} keyPlaceholder="native name" valuePlaceholder="english name" />
          </WideField>
        </CardContent>
      </Card>
    </div>
  );
}

function CostSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Daily Guardrails</CardTitle>
        <CardDescription>Limits reset at midnight UTC. Also visible in the Analytics dashboard.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-0 divide-y">
        <FieldRow label="Daily Budget (USD)" description="Maximum spend across all LLM and search API calls per day." {...fp("cost.max_daily_cost_usd")}>
          <NumberField value={g("cost.max_daily_cost_usd")} onChange={(v) => s("cost.max_daily_cost_usd", v)} min={0.01} max={10000} step={0.5} prefix="$" />
        </FieldRow>
        <FieldRow label="Daily Product Limit" description="Maximum number of products processed per day." {...fp("cost.daily_product_limit")}>
          <NumberField value={g("cost.daily_product_limit")} onChange={(v) => s("cost.daily_product_limit", v)} min={1} max={50000} />
        </FieldRow>
        <FieldRow label="Max Batch Size" description="Maximum products in a single batch enrichment request." {...fp("cost.max_batch_size")}>
          <NumberField value={g("cost.max_batch_size")} onChange={(v) => s("cost.max_batch_size", v)} min={1} max={1000} />
        </FieldRow>
        <FieldRow label="Market Region" description="Appended to search queries for regional relevance — e.g. Slovenia, Germany." {...fp("cost.market_region")}>
          <Input value={g("cost.market_region")} onChange={(e) => s("cost.market_region", e.target.value)} placeholder="e.g. Slovenia" className="w-44" />
        </FieldRow>
      </CardContent>
    </Card>
  );
}

function LLMSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  const modelOptions = [
    { value: "haiku",  label: "Haiku — fast & cheap" },
    { value: "sonnet", label: "Sonnet — smart & thorough" },
  ];

  const phases = [
    { key: "llm.triage_model",   label: "Triage",   description: "Classifies product type and extracts brand/model number." },
    { key: "llm.search_model",   label: "Search",   description: "Ranks and filters search result URLs." },
    { key: "llm.extract_model",  label: "Extract",  description: "Extracts structured data from scraped pages." },
    { key: "llm.gap_fill_model", label: "Gap Fill", description: "Targeted extraction for missing critical fields." },
    { key: "llm.validate_model", label: "Validate", description: "Sanity-checks the final enriched data." },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Model Selection</CardTitle>
        <CardDescription>Choose Haiku for speed and cost efficiency, Sonnet for complex or high-value products.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-0 divide-y">
        {phases.map(({ key, label, description }) => (
          <FieldRow key={key} label={label} description={description} {...fp(key)}>
            <SettingsSelect value={g(key)} onChange={(v) => s(key, v)} options={modelOptions} />
          </FieldRow>
        ))}
      </CardContent>
    </Card>
  );
}

function BrandsSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Known Brands</CardTitle>
          <CardDescription>Helps Claude identify brands during the triage phase. Add brands you frequently sell.</CardDescription>
        </CardHeader>
        <CardContent>
          <WideField label="Brand List" {...fp("brands.known_brands")}>
            <TagInput value={g("brands.known_brands") ?? []} onChange={(v) => s("brands.known_brands", v)} placeholder="BrandName" />
          </WideField>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Brand Country of Origin</CardTitle>
          <CardDescription>Pre-seed country-of-origin for specific brands. Avoids a web lookup during enrichment.</CardDescription>
        </CardHeader>
        <CardContent>
          <WideField label="Brand → Country" {...fp("brands.brand_coo_seeds")}>
            <KVInput value={g("brands.brand_coo_seeds") ?? {}} onChange={(v) => s("brands.brand_coo_seeds", v)} keyPlaceholder="Makita" valuePlaceholder="Japan" />
          </WideField>
        </CardContent>
      </Card>
    </div>
  );
}

function ExportSection({ ctx }: { ctx: FieldCtx }) {
  const { getField: g, setField: s, overrides: ov, resetField: r } = ctx;
  const fp = (key: string) => ({ dotKey: key, overrides: ov, onReset: r });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Export Options</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0 divide-y">
          <FieldRow label="Include Cost Data" description="Add per-product cost columns to the exported CSV." {...fp("export.include_cost_data")}>
            <Switch checked={g("export.include_cost_data")} onCheckedChange={(v) => s("export.include_cost_data", v)} />
          </FieldRow>
          <FieldRow label="Include Enrichment Log" description="Add a full enrichment log column — useful for debugging pipeline decisions." {...fp("export.include_enrichment_log")}>
            <Switch checked={g("export.include_enrichment_log")} onCheckedChange={(v) => s("export.include_enrichment_log", v)} />
          </FieldRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Column Name Overrides</CardTitle>
          <CardDescription>Rename export column headers — useful for localised column names.</CardDescription>
        </CardHeader>
        <CardContent>
          <WideField label="Column Overrides" description="e.g. short_description → Kratek opis" {...fp("export.column_overrides")}>
            <KVInput value={g("export.column_overrides") ?? {}} onChange={(v) => s("export.column_overrides", v)} keyPlaceholder="internal_field" valuePlaceholder="Column Header" />
          </WideField>
        </CardContent>
      </Card>
    </div>
  );
}
