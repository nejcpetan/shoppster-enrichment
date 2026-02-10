"use client";

import { useState, useEffect } from "react";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    ArrowUpDown, ChevronRight,
    Ruler, Weight, Palette, Globe, AlertCircle, CheckCircle2,
    Zap, Loader2, X, AlertTriangle, Brain, Search, FileText, ShieldCheck
} from "lucide-react";
import { fetchAPI } from "@/lib/api";
import { useRouter } from "next/navigation";

// Phase mapping for pipeline progress dots
const PHASES = [
    { key: 'classifying', label: 'Classify', icon: Brain, color: 'purple' },
    { key: 'searching', label: 'Search', icon: Search, color: 'blue' },
    { key: 'extracting', label: 'Extract', icon: FileText, color: 'orange' },
    { key: 'validating', label: 'Validate', icon: ShieldCheck, color: 'green' },
];

const PROCESSING_STATUSES = ['enriching', 'classifying', 'searching', 'extracting', 'validating'];

function getPhaseIndex(status: string): number {
    const idx = PHASES.findIndex(p => p.key === status);
    if (status === 'enriching') return 0;
    if (status === 'done' || status === 'needs_review') return 4;
    return idx;
}

export function ProductTable({ refreshTrigger }: { refreshTrigger: number }) {
    const [products, setProducts] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [processing, setProcessing] = useState(false);
    const router = useRouter();

    useEffect(() => {
        loadProducts();
    }, [refreshTrigger]);

    // Auto-refresh while products are being processed
    useEffect(() => {
        const hasProcessing = products.some(p => PROCESSING_STATUSES.includes(p.status));
        if (hasProcessing || processing) {
            const interval = setInterval(() => {
                loadProducts(true);
            }, 2000); // faster polling for agent step updates
            return () => clearInterval(interval);
        }
    }, [products, processing]);

    const loadProducts = async (silent = false) => {
        if (!silent) setLoading(true);
        try {
            const data = await fetchAPI('/products');
            setProducts(data);
        } catch (error) {
            console.error(error);
        } finally {
            if (!silent) setLoading(false);
        }
    };

    const handleSelectAll = (checked: boolean) => {
        if (checked) {
            setSelectedIds(new Set(products.filter(p => !PROCESSING_STATUSES.includes(p.status)).map(p => p.id)));
        } else {
            setSelectedIds(new Set());
        }
    };

    const handleSelectOne = (id: number, checked: boolean) => {
        const newSelected = new Set(selectedIds);
        if (checked) newSelected.add(id);
        else newSelected.delete(id);
        setSelectedIds(newSelected);
    };

    const handleRunSelected = async () => {
        if (selectedIds.size === 0) return;
        setProcessing(true);
        try {
            await fetchAPI('/products/process-batch', {
                method: 'POST',
                body: JSON.stringify({ product_ids: Array.from(selectedIds) })
            });
            setSelectedIds(new Set());
            setTimeout(() => {
                loadProducts(true);
                setTimeout(() => setProcessing(false), 2000);
            }, 500);
        } catch (e) {
            console.error(e);
            setProcessing(false);
        }
    };

    // Extract enriched value from validation/extraction results
    const getEnrichedVal = (product: any, field: string) => {
        let val = null;
        let unit = null;

        if (product.validation_result) {
            try {
                const data = JSON.parse(product.validation_result).normalized_data;
                if (data?.[field]) { val = data[field].value; unit = data[field].unit; }
            } catch { }
        }

        if (!val && product.extraction_result) {
            try {
                const data = JSON.parse(product.extraction_result);
                if (data?.[field]) { val = data[field].value; unit = data[field].unit; }
            } catch { }
        }

        if (val === null || val === undefined) return <span className="text-zinc-700">-</span>;

        return (
            <span className="font-mono text-zinc-300">
                {val} <span className="text-zinc-600 text-[10px] ml-0.5">{unit}</span>
            </span>
        );
    };

    const getClassification = (product: any) => {
        if (!product.classification_result) return <span className="text-zinc-700">-</span>;
        try {
            const cls = JSON.parse(product.classification_result);
            return (
                <div className="flex flex-col">
                    <span className="text-white font-medium">{cls.brand || "Unknown"}</span>
                    <span className="text-[10px] text-zinc-500 capitalize">{cls.product_type?.replace('_', ' ') || ''}</span>
                </div>
            );
        } catch { return <span className="text-zinc-700">Error</span> }
    };

    return (
        <div className="w-full h-full flex flex-col space-y-4 relative">
            {/* Floating Action Bar */}
            {selectedIds.size > 0 && (
                <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-50 animate-in slide-in-from-bottom-4 fade-in duration-300">
                    <div className="bg-zinc-900 border border-zinc-700 rounded-full shadow-2xl shadow-black/50 px-2 py-2 flex items-center gap-3 pr-4">
                        <span className="bg-zinc-800 text-zinc-300 px-3 py-1 rounded-full text-xs font-bold border border-zinc-700">
                            {selectedIds.size} Selected
                        </span>
                        <Button
                            size="sm"
                            className="rounded-full bg-blue-600 hover:bg-blue-500 text-white h-8 px-4 text-xs font-bold shadow-lg shadow-blue-900/20"
                            onClick={handleRunSelected}
                            disabled={processing}
                        >
                            {processing ? <Loader2 className="w-3 h-3 animate-spin mr-2" /> : <Zap className="w-3 h-3 mr-2 fill-white" />}
                            {processing ? 'Processing...' : 'Run Enrichment'}
                        </Button>
                        <Button
                            size="sm"
                            variant="ghost"
                            className="rounded-full h-8 w-8 text-zinc-500 hover:text-white p-0 hover:bg-zinc-800"
                            onClick={() => setSelectedIds(new Set())}
                        >
                            <X className="w-4 h-4" />
                        </Button>
                    </div>
                </div>
            )}

            {/* Toolbar */}
            <div className="flex items-center justify-between">
                <div className="text-sm text-zinc-500 font-mono">
                    Showing <span className="text-white font-bold">{products.length}</span> products
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="ghost" size="sm" onClick={() => loadProducts(false)} className="text-zinc-500 hover:text-white">
                        <ArrowUpDown className="w-4 h-4 mr-2" />
                        Refresh
                    </Button>
                </div>
            </div>

            {/* Main Table */}
            <div className="w-full rounded-xl border border-zinc-800 bg-zinc-900/40 backdrop-blur-sm overflow-hidden shadow-2xl shadow-black/20 flex-1 relative">
                <div className="overflow-x-auto h-full max-h-[600px]">
                    <Table className="w-full whitespace-nowrap">
                        <TableHeader className="bg-zinc-950/80 border-b border-zinc-800/80 sticky top-0 z-10 backdrop-blur-md">
                            <TableRow className="border-none hover:bg-transparent h-10">
                                <TableHead className="w-[50px] pl-4">
                                    <Checkbox
                                        className="border-zinc-700 data-[state=checked]:bg-purple-600 data-[state=checked]:border-purple-600"
                                        checked={products.length > 0 && selectedIds.size === products.filter(p => !PROCESSING_STATUSES.includes(p.status)).length}
                                        onCheckedChange={(checked) => handleSelectAll(!!checked)}
                                    />
                                </TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[220px]">Status</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[280px]">Product / EAN</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[130px]">Brand & Type</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[100px] text-center"><Ruler className="w-3 h-3 mx-auto mb-1" /> Dim.</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[80px] text-center"><Weight className="w-3 h-3 mx-auto mb-1" /> Wgt.</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[80px] text-center"><Palette className="w-3 h-3 mx-auto mb-1" /> Color</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[80px] text-center"><Globe className="w-3 h-3 mx-auto mb-1" /> Origin</TableHead>
                                <TableHead className="w-[40px]"></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {products.map((product) => {
                                const isActive = PROCESSING_STATUSES.includes(product.status);
                                const phaseIdx = getPhaseIndex(product.status);

                                return (
                                    <TableRow
                                        key={product.id}
                                        className={`
                                            border-zinc-800/50 transition-colors group cursor-pointer relative
                                            ${isActive ? 'bg-blue-950/20 border-l-2 border-l-blue-500' : ''}
                                            ${selectedIds.has(product.id) ? 'bg-purple-900/10 border-purple-500/20 hover:bg-purple-900/20' : 'hover:bg-zinc-800/30'}
                                        `}
                                        onClick={() => router.push(`/products/${product.id}`)}
                                    >
                                        {/* Checkbox / Spinner */}
                                        <TableCell className="pl-4 relative" onClick={(e) => e.stopPropagation()}>
                                            {isActive ? (
                                                <div className="flex items-center justify-center">
                                                    <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
                                                </div>
                                            ) : (
                                                <Checkbox
                                                    className="border-zinc-700"
                                                    checked={selectedIds.has(product.id)}
                                                    onCheckedChange={(checked) => handleSelectOne(product.id, !!checked)}
                                                />
                                            )}
                                        </TableCell>

                                        {/* Status + Pipeline Progress + Agent Step */}
                                        <TableCell>
                                            <div className="flex flex-col gap-1.5">
                                                {/* Status Badge */}
                                                <StatusBadge status={product.status} />

                                                {/* Pipeline Progress — labeled phase pills when active */}
                                                {isActive && (
                                                    <div className="flex items-center gap-0.5">
                                                        {PHASES.map((phase, i) => {
                                                            const isDone = i < phaseIdx;
                                                            const isCurrent = i === phaseIdx;
                                                            const PhaseIcon = phase.icon;

                                                            const pillColors: Record<string, { done: string, active: string, pending: string }> = {
                                                                purple: { done: 'bg-purple-500/20 text-purple-400', active: 'bg-purple-500/30 text-purple-300 ring-1 ring-purple-500/50', pending: 'bg-zinc-800/50 text-zinc-600' },
                                                                blue: { done: 'bg-blue-500/20 text-blue-400', active: 'bg-blue-500/30 text-blue-300 ring-1 ring-blue-500/50', pending: 'bg-zinc-800/50 text-zinc-600' },
                                                                orange: { done: 'bg-orange-500/20 text-orange-400', active: 'bg-orange-500/30 text-orange-300 ring-1 ring-orange-500/50', pending: 'bg-zinc-800/50 text-zinc-600' },
                                                                green: { done: 'bg-emerald-500/20 text-emerald-400', active: 'bg-emerald-500/30 text-emerald-300 ring-1 ring-emerald-500/50', pending: 'bg-zinc-800/50 text-zinc-600' },
                                                            };
                                                            const colors = pillColors[phase.color];
                                                            const pillClass = isDone ? colors.done : isCurrent ? colors.active : colors.pending;

                                                            return (
                                                                <div key={phase.key} className="flex items-center gap-0.5">
                                                                    <div className={`flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[9px] font-semibold transition-all duration-300 ${pillClass}`}>
                                                                        <PhaseIcon className={`w-2.5 h-2.5 ${isCurrent ? 'animate-pulse' : ''}`} />
                                                                        {isCurrent && <span>{phase.label}</span>}
                                                                    </div>
                                                                    {i < PHASES.length - 1 && (
                                                                        <div className={`w-1.5 h-px ${isDone ? 'bg-zinc-500' : 'bg-zinc-800'}`} />
                                                                    )}
                                                                </div>
                                                            );
                                                        })}
                                                    </div>
                                                )}

                                                {/* Compact dots for completed products */}
                                                {!isActive && (product.status === 'done' || product.status === 'needs_review') && (
                                                    <div className="flex items-center gap-1">
                                                        {PHASES.map((phase, i) => (
                                                            <div key={phase.key} className="flex items-center gap-0.5">
                                                                <div className={`w-1.5 h-1.5 rounded-full ${phase.color === 'purple' ? 'bg-purple-400' :
                                                                    phase.color === 'blue' ? 'bg-blue-400' :
                                                                        phase.color === 'orange' ? 'bg-orange-400' : 'bg-emerald-400'
                                                                    }`} />
                                                                {i < PHASES.length - 1 && <div className="w-2 h-px bg-zinc-500" />}
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}

                                                {/* Current agent step message — prominent when active */}
                                                {isActive && product.current_step && (
                                                    <span className="text-[11px] text-blue-300/80 truncate max-w-[200px] leading-tight font-mono">
                                                        ↳ {product.current_step}
                                                    </span>
                                                )}
                                            </div>
                                        </TableCell>

                                        {/* Product Name & EAN */}
                                        <TableCell>
                                            <div className="flex flex-col gap-1 max-w-[260px]">
                                                <span className={`font-medium truncate ${isActive ? 'text-blue-200' : 'text-zinc-200'}`}>
                                                    {product.product_name}
                                                </span>
                                                <span className="text-[10px] font-mono text-zinc-500">{product.ean}</span>
                                            </div>
                                        </TableCell>

                                        {/* Brand & Type */}
                                        <TableCell>
                                            {getClassification(product)}
                                        </TableCell>

                                        {/* Dimensions */}
                                        <TableCell className="text-center">
                                            <div className="flex flex-col items-center gap-0.5 text-xs">
                                                <div className="flex items-center gap-1">
                                                    <span className="text-zinc-500 text-[9px]">L:</span> {getEnrichedVal(product, 'length')}
                                                </div>
                                                <div className="flex items-center gap-1">
                                                    <span className="text-zinc-500 text-[9px]">W:</span> {getEnrichedVal(product, 'width')}
                                                </div>
                                                <div className="flex items-center gap-1">
                                                    <span className="text-zinc-500 text-[9px]">H:</span> {getEnrichedVal(product, 'height')}
                                                </div>
                                            </div>
                                        </TableCell>

                                        {/* Weight */}
                                        <TableCell className="text-center">
                                            {getEnrichedVal(product, 'weight')}
                                        </TableCell>

                                        {/* Color */}
                                        <TableCell className="text-center">
                                            {getEnrichedVal(product, 'color')}
                                        </TableCell>

                                        {/* Origin */}
                                        <TableCell className="text-center">
                                            {getEnrichedVal(product, 'country_of_origin')}
                                        </TableCell>

                                        {/* Chevron */}
                                        <TableCell>
                                            <Button variant="ghost" size="icon" className="h-8 w-8 text-zinc-500 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity">
                                                <ChevronRight className="w-4 h-4" />
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </div>
            </div>

            {/* Footer */}

        </div>
    );
}

/* --- Status Badge Component --- */
function StatusBadge({ status }: { status: string }) {
    const statusConfig: Record<string, { color: string, icon?: any }> = {
        done: { color: 'bg-emerald-500/10 text-emerald-500', icon: CheckCircle2 },
        error: { color: 'bg-red-500/10 text-red-500', icon: AlertCircle },
        needs_review: { color: 'bg-yellow-500/10 text-yellow-500', icon: AlertTriangle },
        classifying: { color: 'bg-purple-500/10 text-purple-400 ring-1 ring-purple-500/30', icon: Brain },
        searching: { color: 'bg-blue-500/10 text-blue-400 ring-1 ring-blue-500/30', icon: Search },
        extracting: { color: 'bg-orange-500/10 text-orange-400 ring-1 ring-orange-500/30', icon: FileText },
        validating: { color: 'bg-green-500/10 text-green-400 ring-1 ring-green-500/30', icon: ShieldCheck },
        enriching: { color: 'bg-indigo-500/10 text-indigo-400 ring-1 ring-indigo-500/30', icon: Loader2 },
        pending: { color: 'bg-zinc-800 text-zinc-500' },
    };

    const config = statusConfig[status] || statusConfig.pending;
    const Icon = config.icon;
    const isActive = PROCESSING_STATUSES.includes(status);

    return (
        <Badge variant="outline" className={`border-0 uppercase text-[9px] tracking-wider font-bold px-2 py-0.5 w-fit ${config.color}`}>
            {Icon && <Icon className={`w-3 h-3 mr-1 ${isActive ? 'animate-spin' : ''}`} />}
            {(status || 'pending').replace('_', ' ')}
        </Badge>
    );
}
