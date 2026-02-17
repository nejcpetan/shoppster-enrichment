"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { UploadCSV } from "@/components/UploadCSV";
import { ProductTable } from "@/components/ProductTable";
import { Button } from "@/components/ui/button";
import {
  Search, Filter, Download, Layers,
  Zap, MoreHorizontal, RefreshCw, CheckCircle2,
  AlertCircle, Clock, Loader2, AlertTriangle
} from "lucide-react";
import { fetchAPI } from "@/lib/api";

interface DashboardStats {
  total: number;
  pending: number;
  done: number;
  errors: number;
  needs_review: number;
  processing: number;
}

export default function Home() {
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadStats = async () => {
    try {
      const data = await fetchAPI('/dashboard/stats');
      setStats(data);
    } catch (error) {
      console.error("Failed to load stats", error);
    }
  };

  // Load stats initially and on refresh trigger
  useEffect(() => {
    loadStats();
  }, [refreshTrigger]);

  // Debounced stats refresh — called by ProductTable's SSE handler
  const handleStatusChange = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      loadStats();
    }, 500);
  }, []);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  const processing = (stats?.processing ?? 0) > 0;

  const handleUploadSuccess = () => {
    setRefreshTrigger(prev => prev + 1);
  };

  const handleEnrichAll = async () => {
    try {
      await fetchAPI('/products/process-all', { method: 'POST' });
      // SSE will push status updates — just refresh stats
      loadStats();
      setRefreshTrigger(prev => prev + 1);
    } catch (e) {
      console.error(e);
    }
  };

  const handleExport = () => {
    window.open('http://localhost:8000/api/export', '_blank');
  };

  const statCards = [
    { label: "Total Products", value: stats?.total ?? 0, icon: <Layers className="w-4 h-4" />, color: "text-zinc-300" },
    { label: "Pending", value: stats?.pending ?? 0, icon: <Clock className="w-4 h-4" />, color: "text-zinc-400" },
    { label: "Processing", value: stats?.processing ?? 0, icon: <Loader2 className={`w-4 h-4 ${(stats?.processing ?? 0) > 0 ? 'animate-spin' : ''}`} />, color: "text-indigo-400" },
    { label: "Complete", value: stats?.done ?? 0, icon: <CheckCircle2 className="w-4 h-4" />, color: "text-emerald-400" },
    { label: "Needs Review", value: stats?.needs_review ?? 0, icon: <AlertTriangle className="w-4 h-4" />, color: "text-yellow-400" },
    { label: "Errors", value: stats?.errors ?? 0, icon: <AlertCircle className="w-4 h-4" />, color: "text-red-400" },
  ];

  return (
    <div className="min-h-screen bg-black text-zinc-100 font-sans selection:bg-purple-500/30">
      {/* Top Navigation Bar */}
      <nav className="border-b border-zinc-800 bg-black/80 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-[1800px] mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="font-bold text-lg tracking-tight text-white">
              Enrichment Engine
            </span>
          </div>

          <div className="flex items-center gap-4">
            <Button
              variant="outline"
              size="sm"
              className="border-zinc-800 bg-zinc-900/50 hover:bg-zinc-800 text-zinc-400 hover:text-white transition-all"
              onClick={handleExport}
            >
              <Download className="w-4 h-4 mr-2" />
              Export Data
            </Button>
            <div className="h-6 w-px bg-zinc-800 mx-2"></div>
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center border border-zinc-700">
                <span className="text-xs font-bold">JD</span>
              </div>
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main className="w-full px-6 py-8 space-y-6 max-w-[1800px] mx-auto">
        {/* Stats Cards */}
        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            {statCards.map((card) => (
              <div key={card.label} className="bg-zinc-900/50 border border-zinc-800 rounded-lg p-4 flex flex-col gap-1">
                <div className="flex items-center gap-2 text-zinc-500 text-xs uppercase tracking-wider">
                  <span className={card.color}>{card.icon}</span>
                  {card.label}
                </div>
                <div className={`text-2xl font-bold tabular-nums ${card.color}`}>
                  {card.value}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Header Section with Upload and Search */}
        <div className="flex flex-col md:flex-row gap-6 items-start justify-between">
          <div className="w-full max-w-xl space-y-4">
            <UploadCSV onUploadSuccess={handleUploadSuccess} />
          </div>

          {/* Global Actions */}
          <div className="flex items-center gap-3">
            <Button
              size="lg"
              className={`
                relative overflow-hidden transition-all duration-300 shadow-lg font-medium tracking-wide
                ${processing
                  ? 'bg-zinc-900 text-zinc-500 cursor-not-allowed border border-zinc-800'
                  : 'bg-zinc-100 hover:bg-white text-zinc-950 border border-transparent shadow-zinc-500/10'}
              `}
              onClick={handleEnrichAll}
              disabled={processing}
            >
              {processing ? (
                <>
                  <RefreshCw className="w-5 h-5 mr-2 animate-spin" />
                  Processing Batch...
                </>
              ) : (
                <>
                  <Zap className="w-5 h-5 mr-2 fill-current" />
                  Enrich All Pending
                </>
              )}
            </Button>
          </div>
        </div>

        {/* Full Width Table Section */}
        <section className="space-y-4 h-[calc(100vh-400px)] min-h-[500px]">
          <ProductTable refreshTrigger={refreshTrigger} onStatusChange={handleStatusChange} />
        </section>
      </main>
    </div>
  );
}
