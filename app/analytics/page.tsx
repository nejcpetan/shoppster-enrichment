"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { fetchAPI } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
    ArrowLeft, DollarSign, Zap, TrendingUp, Shield,
    RefreshCw, Save, Activity, Database, Cpu, ChevronRight,
} from "lucide-react";

// ─── Types ───────────────────────────────────────────────────────────────────

interface CostStats {
    processed_today: number;
    currently_processing: number;
    total_cost_today_usd: number;
    total_input_tokens_today: number;
    total_output_tokens_today: number;
    service_costs_today: Record<string, number>;
    total_products: number;
    total_products_with_cost: number;
    total_cost_all_time_usd: number;
    avg_cost_per_product_usd: number;
    daily_product_limit: number;
    max_batch_size: number;
    max_daily_cost_usd: number;
    remaining_products: number;
    remaining_budget_usd: number;
}

interface Limits {
    daily_product_limit: number;
    max_batch_size: number;
    max_daily_cost_usd: number;
}

// ─── Component ───────────────────────────────────────────────────────────────

export default function AnalyticsPage() {
    const [stats, setStats] = useState<CostStats | null>(null);
    const [limits, setLimits] = useState<Limits>({
        daily_product_limit: 200,
        max_batch_size: 50,
        max_daily_cost_usd: 50.0,
    });
    const [editLimits, setEditLimits] = useState<Limits>({
        daily_product_limit: 200,
        max_batch_size: 50,
        max_daily_cost_usd: 50.0,
    });
    const [saving, setSaving] = useState(false);
    const [saveMessage, setSaveMessage] = useState("");
    const [loading, setLoading] = useState(true);

    const loadData = useCallback(async () => {
        try {
            const [costData, limitsData] = await Promise.all([
                fetchAPI("/dashboard/costs"),
                fetchAPI("/dashboard/limits"),
            ]);
            setStats(costData);
            setLimits(limitsData);
            setEditLimits(limitsData);
        } catch (error) {
            console.error("Failed to load analytics data", error);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadData();
        const interval = setInterval(loadData, 15000); // Auto-refresh every 15s
        return () => clearInterval(interval);
    }, [loadData]);

    const handleSaveLimits = async () => {
        setSaving(true);
        setSaveMessage("");
        try {
            const res = await fetchAPI("/dashboard/limits", {
                method: "PUT",
                body: JSON.stringify(editLimits),
            });
            setLimits(res.limits);
            setEditLimits(res.limits);
            setSaveMessage("Limits updated successfully");
            loadData(); // Refresh stats
            setTimeout(() => setSaveMessage(""), 3000);
        } catch (error: any) {
            setSaveMessage(`Error: ${error.message}`);
        } finally {
            setSaving(false);
        }
    };

    const hasLimitsChanged =
        editLimits.daily_product_limit !== limits.daily_product_limit ||
        editLimits.max_batch_size !== limits.max_batch_size ||
        editLimits.max_daily_cost_usd !== limits.max_daily_cost_usd;

    // ─── Helpers ─────────────────────────────────────────────────────────────

    const formatCost = (v: number) => `$${v.toFixed(4)}`;
    const formatTokens = (v: number) =>
        v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 1_000 ? `${(v / 1_000).toFixed(1)}K` : `${v}`;

    const budgetUsedPct = stats
        ? Math.min(100, (stats.total_cost_today_usd / stats.max_daily_cost_usd) * 100)
        : 0;
    const productsUsedPct = stats
        ? Math.min(100, (stats.processed_today / stats.daily_product_limit) * 100)
        : 0;

    // Service label mapping for nicer display
    const svcLabel: Record<string, string> = {
        claude_haiku: "Claude Haiku",
        claude_sonnet: "Claude Sonnet",
        gemini_flash: "Gemini Flash",
        firecrawl: "Firecrawl",
        tavily: "Tavily",
    };

    // ─── Render ──────────────────────────────────────────────────────────────

    if (loading) {
        return (
            <div className="h-screen bg-black text-zinc-100 flex items-center justify-center">
                <RefreshCw className="w-6 h-6 animate-spin text-zinc-500" />
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-black text-zinc-100 font-sans selection:bg-purple-500/30">
            {/* Navigation */}
            <nav className="border-b border-zinc-800 bg-black/80 backdrop-blur-md sticky top-0 z-50">
                <div className="max-w-[1400px] mx-auto px-6 h-16 flex items-center justify-between">
                    <div className="flex items-center gap-4">
                        <Link href="/">
                            <Button
                                variant="ghost"
                                size="sm"
                                className="text-zinc-400 hover:text-white transition-colors"
                            >
                                <ArrowLeft className="w-4 h-4 mr-2" />
                                Back to Products
                            </Button>
                        </Link>
                        <div className="h-6 w-px bg-zinc-800" />
                        <h1 className="font-bold text-lg tracking-tight text-white flex items-center gap-2">
                            <Activity className="w-5 h-5 text-indigo-400" />
                            Cost Analytics
                        </h1>
                    </div>
                    <Button
                        variant="outline"
                        size="sm"
                        onClick={loadData}
                        className="border-zinc-800 bg-zinc-900/50 hover:bg-zinc-800 text-zinc-400 hover:text-white transition-all"
                    >
                        <RefreshCw className="w-4 h-4 mr-2" />
                        Refresh
                    </Button>
                </div>
            </nav>

            <main className="max-w-[1400px] mx-auto px-6 py-8 space-y-8">

                {/* ── Top Summary Cards ─────────────────────────────────────────── */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <SummaryCard
                        label="Today's Spend"
                        value={formatCost(stats?.total_cost_today_usd ?? 0)}
                        sub={`Budget: ${formatCost(stats?.max_daily_cost_usd ?? 0)}`}
                        icon={<DollarSign className="w-5 h-5" />}
                        color="text-emerald-400"
                        bgGlow="bg-emerald-500/5"
                    />
                    <SummaryCard
                        label="Products Today"
                        value={`${stats?.processed_today ?? 0}`}
                        sub={`Limit: ${stats?.daily_product_limit ?? 0}`}
                        icon={<Zap className="w-5 h-5" />}
                        color="text-indigo-400"
                        bgGlow="bg-indigo-500/5"
                    />
                    <SummaryCard
                        label="All-Time Spend"
                        value={formatCost(stats?.total_cost_all_time_usd ?? 0)}
                        sub={`${stats?.total_products_with_cost ?? 0} products tracked`}
                        icon={<TrendingUp className="w-5 h-5" />}
                        color="text-amber-400"
                        bgGlow="bg-amber-500/5"
                    />
                    <SummaryCard
                        label="Avg Cost / Product"
                        value={formatCost(stats?.avg_cost_per_product_usd ?? 0)}
                        sub={`${stats?.total_products ?? 0} total products`}
                        icon={<Database className="w-5 h-5" />}
                        color="text-cyan-400"
                        bgGlow="bg-cyan-500/5"
                    />
                </div>

                {/* ── Budget & Capacity Gauges ─────────────────────────────────── */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Budget Gauge */}
                    <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-6 space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                                <DollarSign className="w-4 h-4 text-emerald-400" />
                                Daily Budget Usage
                            </h3>
                            <span className="text-xs text-zinc-500">
                                {formatCost(stats?.total_cost_today_usd ?? 0)} / {formatCost(stats?.max_daily_cost_usd ?? 0)}
                            </span>
                        </div>
                        <div className="relative h-4 bg-zinc-800 rounded-full overflow-hidden">
                            <div
                                className={`absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out ${budgetUsedPct > 90 ? "bg-red-500" : budgetUsedPct > 70 ? "bg-amber-500" : "bg-emerald-500"
                                    }`}
                                style={{ width: `${budgetUsedPct}%` }}
                            />
                            <div className="absolute inset-0 bg-gradient-to-r from-transparent to-white/5" />
                        </div>
                        <div className="flex justify-between text-xs text-zinc-500">
                            <span>{budgetUsedPct.toFixed(1)}% used</span>
                            <span>Remaining: {formatCost(stats?.remaining_budget_usd ?? 0)}</span>
                        </div>
                    </div>

                    {/* Product Capacity Gauge */}
                    <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-6 space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                                <Cpu className="w-4 h-4 text-indigo-400" />
                                Daily Product Capacity
                            </h3>
                            <span className="text-xs text-zinc-500">
                                {stats?.processed_today ?? 0} / {stats?.daily_product_limit ?? 0}
                            </span>
                        </div>
                        <div className="relative h-4 bg-zinc-800 rounded-full overflow-hidden">
                            <div
                                className={`absolute inset-y-0 left-0 rounded-full transition-all duration-700 ease-out ${productsUsedPct > 90 ? "bg-red-500" : productsUsedPct > 70 ? "bg-amber-500" : "bg-indigo-500"
                                    }`}
                                style={{ width: `${productsUsedPct}%` }}
                            />
                            <div className="absolute inset-0 bg-gradient-to-r from-transparent to-white/5" />
                        </div>
                        <div className="flex justify-between text-xs text-zinc-500">
                            <span>{productsUsedPct.toFixed(1)}% used</span>
                            <span>Remaining: {stats?.remaining_products ?? 0} products</span>
                        </div>
                    </div>
                </div>

                {/* ── Two-Column: Token Breakdown + Service Costs ──────────────── */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* Token Usage */}
                    <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-6 space-y-5">
                        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                            <Zap className="w-4 h-4 text-violet-400" />
                            Token Usage Today
                        </h3>
                        <div className="space-y-4">
                            <TokenRow
                                label="Input Tokens"
                                value={stats?.total_input_tokens_today ?? 0}
                                color="text-blue-400"
                            />
                            <TokenRow
                                label="Output Tokens"
                                value={stats?.total_output_tokens_today ?? 0}
                                color="text-purple-400"
                            />
                            <div className="border-t border-zinc-800 pt-3">
                                <TokenRow
                                    label="Total Tokens"
                                    value={(stats?.total_input_tokens_today ?? 0) + (stats?.total_output_tokens_today ?? 0)}
                                    color="text-zinc-200"
                                    bold
                                />
                            </div>
                        </div>
                    </div>

                    {/* Service Costs */}
                    <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-6 space-y-5">
                        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                            <TrendingUp className="w-4 h-4 text-cyan-400" />
                            Cost by Service (Today)
                        </h3>
                        {stats && Object.keys(stats.service_costs_today).length > 0 ? (
                            <div className="space-y-3">
                                {Object.entries(stats.service_costs_today)
                                    .sort(([, a], [, b]) => b - a)
                                    .map(([service, cost]) => (
                                        <ServiceCostRow
                                            key={service}
                                            service={svcLabel[service] ?? service}
                                            cost={cost}
                                            totalCost={stats.total_cost_today_usd}
                                        />
                                    ))}
                            </div>
                        ) : (
                            <div className="text-zinc-500 text-sm py-4 text-center">
                                No costs recorded today yet.
                            </div>
                        )}
                    </div>
                </div>

                {/* ── Guardrail Configuration ─────────────────────────────────── */}
                <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-6 space-y-6">
                    <div className="flex items-center justify-between">
                        <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider flex items-center gap-2">
                            <Shield className="w-4 h-4 text-amber-400" />
                            Cost Guardrails
                        </h3>
                        {saveMessage && (
                            <span
                                className={`text-xs px-3 py-1 rounded-full ${saveMessage.startsWith("Error")
                                        ? "bg-red-500/10 text-red-400 border border-red-500/20"
                                        : "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
                                    }`}
                            >
                                {saveMessage}
                            </span>
                        )}
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                        <LimitInput
                            label="Daily Product Limit"
                            description="Max products processed per day"
                            value={editLimits.daily_product_limit}
                            onChange={(v) => setEditLimits((p) => ({ ...p, daily_product_limit: v }))}
                            min={1}
                            max={10000}
                            step={1}
                            type="number"
                        />
                        <LimitInput
                            label="Max Batch Size"
                            description="Max products per batch request"
                            value={editLimits.max_batch_size}
                            onChange={(v) => setEditLimits((p) => ({ ...p, max_batch_size: v }))}
                            min={1}
                            max={1000}
                            step={1}
                            type="number"
                        />
                        <LimitInput
                            label="Daily Budget (USD)"
                            description="Max daily spend in USD"
                            value={editLimits.max_daily_cost_usd}
                            onChange={(v) => setEditLimits((p) => ({ ...p, max_daily_cost_usd: v }))}
                            min={0.01}
                            max={1000}
                            step={0.50}
                            type="currency"
                        />
                    </div>

                    <div className="flex items-center justify-end gap-3 pt-2">
                        <Button
                            variant="outline"
                            size="sm"
                            className="border-zinc-700 text-zinc-400 hover:text-white hover:bg-zinc-800"
                            onClick={() => setEditLimits(limits)}
                            disabled={!hasLimitsChanged}
                        >
                            Reset
                        </Button>
                        <Button
                            size="sm"
                            className={`transition-all duration-200 ${hasLimitsChanged
                                    ? "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-500/20"
                                    : "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                                }`}
                            onClick={handleSaveLimits}
                            disabled={!hasLimitsChanged || saving}
                        >
                            {saving ? (
                                <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                            ) : (
                                <Save className="w-4 h-4 mr-2" />
                            )}
                            Save Limits
                        </Button>
                    </div>
                </div>

                {/* ── Live Processing Status ──────────────────────────────────── */}
                {(stats?.currently_processing ?? 0) > 0 && (
                    <div className="bg-indigo-500/5 border border-indigo-500/20 rounded-xl p-5 flex items-center gap-4">
                        <div className="flex-shrink-0">
                            <RefreshCw className="w-5 h-5 text-indigo-400 animate-spin" />
                        </div>
                        <div>
                            <p className="text-sm font-medium text-indigo-300">
                                {stats?.currently_processing} product{(stats?.currently_processing ?? 0) > 1 ? "s" : ""} currently processing
                            </p>
                            <p className="text-xs text-zinc-500 mt-0.5">
                                Costs will update when processing completes. Auto-refreshing every 15s.
                            </p>
                        </div>
                    </div>
                )}
            </main>
        </div>
    );
}

// ─── Sub-Components ──────────────────────────────────────────────────────────

function SummaryCard({
    label, value, sub, icon, color, bgGlow,
}: {
    label: string; value: string; sub: string;
    icon: React.ReactNode; color: string; bgGlow: string;
}) {
    return (
        <div className={`relative overflow-hidden bg-zinc-900/60 border border-zinc-800 rounded-xl p-5 space-y-2 group hover:border-zinc-700 transition-colors`}>
            <div className={`absolute -top-8 -right-8 w-24 h-24 rounded-full ${bgGlow} blur-2xl opacity-50 group-hover:opacity-100 transition-opacity`} />
            <div className="relative">
                <div className={`flex items-center gap-2 text-xs uppercase tracking-wider text-zinc-500`}>
                    <span className={color}>{icon}</span>
                    {label}
                </div>
                <div className={`text-2xl font-bold tabular-nums mt-1 ${color}`}>{value}</div>
                <div className="text-xs text-zinc-500 mt-1">{sub}</div>
            </div>
        </div>
    );
}

function TokenRow({
    label, value, color, bold = false,
}: {
    label: string; value: number; color: string; bold?: boolean;
}) {
    const formatted =
        value >= 1_000_000
            ? `${(value / 1_000_000).toFixed(2)}M`
            : value >= 1_000
                ? `${(value / 1_000).toFixed(1)}K`
                : `${value}`;

    return (
        <div className="flex items-center justify-between">
            <span className={`text-sm ${bold ? "font-semibold text-zinc-200" : "text-zinc-400"}`}>{label}</span>
            <span className={`text-sm font-mono tabular-nums ${bold ? "font-bold" : ""} ${color}`}>
                {formatted}
            </span>
        </div>
    );
}

function ServiceCostRow({
    service, cost, totalCost,
}: {
    service: string; cost: number; totalCost: number;
}) {
    const pct = totalCost > 0 ? (cost / totalCost) * 100 : 0;

    // Assign colors by service
    const colorMap: Record<string, string> = {
        "Claude Haiku": "bg-violet-500",
        "Claude Sonnet": "bg-purple-500",
        "Gemini Flash": "bg-blue-500",
        "Firecrawl": "bg-orange-500",
        "Tavily": "bg-cyan-500",
    };
    const barColor = colorMap[service] ?? "bg-zinc-500";

    return (
        <div className="space-y-1.5">
            <div className="flex items-center justify-between">
                <span className="text-sm text-zinc-300">{service}</span>
                <span className="text-sm font-mono tabular-nums text-zinc-400">
                    ${cost.toFixed(4)} <span className="text-zinc-600">({pct.toFixed(0)}%)</span>
                </span>
            </div>
            <div className="relative h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div
                    className={`absolute inset-y-0 left-0 rounded-full ${barColor} transition-all duration-500`}
                    style={{ width: `${Math.max(2, pct)}%` }}
                />
            </div>
        </div>
    );
}

function LimitInput({
    label, description, value, onChange, min, max, step, type,
}: {
    label: string; description: string; value: number;
    onChange: (v: number) => void;
    min: number; max: number; step: number;
    type: "number" | "currency";
}) {
    return (
        <div className="space-y-2">
            <label className="text-sm font-medium text-zinc-300">{label}</label>
            <p className="text-xs text-zinc-500">{description}</p>
            <div className="relative mt-1">
                {type === "currency" && (
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500 text-sm">$</span>
                )}
                <input
                    type="number"
                    value={value}
                    onChange={(e) => {
                        const v = type === "currency" ? parseFloat(e.target.value) : parseInt(e.target.value, 10);
                        if (!isNaN(v) && v >= min && v <= max) {
                            onChange(v);
                        }
                    }}
                    min={min}
                    max={max}
                    step={step}
                    className={`w-full bg-zinc-800/80 border border-zinc-700 rounded-lg px-3 py-2.5 text-sm font-mono
            text-zinc-200 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/30 outline-none transition-all
            ${type === "currency" ? "pl-7" : ""}`}
                />
            </div>
        </div>
    );
}
