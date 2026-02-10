"use client";

import { useState, useEffect } from "react";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    MoreHorizontal, ArrowUpDown, ChevronRight,
    Box, Ruler, Weight, Palette, Globe, Layers, AlertCircle, CheckCircle2,
    Zap, Loader2, X, AlertTriangle
} from "lucide-react";
import { fetchAPI } from "@/lib/api";
import { useRouter } from "next/navigation";

export function ProductTable({ refreshTrigger }: { refreshTrigger: number }) {
    const [products, setProducts] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [processing, setProcessing] = useState(false);
    const router = useRouter();

    useEffect(() => {
        loadProducts();
    }, [refreshTrigger]);

    // Auto-refresh if items are in a processing state
    useEffect(() => {
        const processingStatuses = ['enriching', 'classifying', 'searching', 'extracting', 'validating'];
        const hasProcessing = products.some(p => processingStatuses.includes(p.status));
        if (hasProcessing || processing) {
            const interval = setInterval(() => {
                loadProducts(true); // silent refresh
            }, 3000);
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
            setSelectedIds(new Set(products.map(p => p.id)));
        } else {
            setSelectedIds(new Set());
        }
    };

    const handleSelectOne = (id: number, checked: boolean) => {
        const newSelected = new Set(selectedIds);
        if (checked) {
            newSelected.add(id);
        } else {
            newSelected.delete(id);
        }
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
            // trigger refresh to visually update statuses
            setTimeout(() => {
                loadProducts(true);
                // Keep processing true for a bit to show feedback
                setTimeout(() => setProcessing(false), 2000);
            }, 500);
        } catch (e) {
            console.error(e);
            setProcessing(false);
        }
    };

    // Helper to extract enriched value safely
    const getEnrichedVal = (product: any, field: string) => {
        let val = null;
        let unit = null;

        // Priority 1: Validation Result (Golden Record)
        if (product.validation_result) {
            try {
                const data = JSON.parse(product.validation_result).normalized_data;
                if (data && data[field]) {
                    val = data[field].value;
                    unit = data[field].unit;
                }
            } catch (e) { }
        }

        // Priority 2: Extraction Result
        if (!val && product.extraction_result) {
            try {
                const data = JSON.parse(product.extraction_result);
                if (data && data[field]) {
                    val = data[field].value;
                    unit = data[field].unit;
                }
            } catch (e) { }
        }

        if (val === null || val === undefined) return <span className="text-zinc-700">-</span>;

        return (
            <span className="font-mono text-zinc-300">
                {val} <span className="text-zinc-600 text-[10px] ml-0.5">{unit}</span>
            </span>
        );
    };

    // Helper for Classification
    const getClassification = (product: any) => {
        if (!product.classification_result) return <span className="text-zinc-700">-</span>;
        try {
            const cls = JSON.parse(product.classification_result);
            return (
                <div className="flex flex-col">
                    <span className="text-white font-medium">{cls.brand || "Unknown"}</span>
                    <span className="text-[10px] text-zinc-500 capitalize">{cls.product_type ? cls.product_type.replace('_', ' ') : ''}</span>
                </div>
            )
        } catch (e) { return <span className="text-zinc-700">Error</span> }
    };

    return (
        <div className="w-full h-full flex flex-col space-y-4 relative">
            {/* Floating Action Bar for Selection */}
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

            {/* Toolbar with Refresh and Count */}
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

            {/* Main Table Container */}
            <div className="w-full rounded-xl border border-zinc-800 bg-zinc-900/40 backdrop-blur-sm overflow-hidden shadow-2xl shadow-black/20 flex-1 relative">
                <div className="overflow-x-auto h-full max-h-[600px]"> {/* Fixed height for scroll */}
                    <Table className="w-full whitespace-nowrap">
                        <TableHeader className="bg-zinc-950/80 border-b border-zinc-800/80 sticky top-0 z-10 backdrop-blur-md">
                            <TableRow className="border-none hover:bg-transparent h-10">
                                <TableHead className="w-[50px] pl-4">
                                    <Checkbox
                                        className="border-zinc-700 data-[state=checked]:bg-purple-600 data-[state=checked]:border-purple-600"
                                        checked={products.length > 0 && selectedIds.size === products.length}
                                        onCheckedChange={(checked) => handleSelectAll(!!checked)}
                                    />
                                </TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[100px]">Status</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[300px]">Product / EAN</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[150px]">Brand & Type</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[100px] text-center"><Ruler className="w-3 h-3 mx-auto mb-1" /> Dim.</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[80px] text-center"><Weight className="w-3 h-3 mx-auto mb-1" /> Wgt.</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[100px] text-center"><Palette className="w-3 h-3 mx-auto mb-1" /> Color</TableHead>
                                <TableHead className="text-[10px] uppercase tracking-widest font-bold text-zinc-500 w-[100px] text-center"><Globe className="w-3 h-3 mx-auto mb-1" /> Origin</TableHead>
                                <TableHead className="w-[50px]"></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {products.map((product) => (
                                <TableRow
                                    key={product.id}
                                    className={`
                                        border-zinc-800/50 transition-colors group cursor-pointer
                                        ${selectedIds.has(product.id) ? 'bg-purple-900/10 border-purple-500/20 hover:bg-purple-900/20' : 'hover:bg-zinc-800/30'}
                                    `}
                                    onClick={() => router.push(`/products/${product.id}`)}
                                >
                                    <TableCell className="pl-4 relative" onClick={(e) => e.stopPropagation()}>
                                        {(() => {
                                            const processingStatuses = ['enriching', 'classifying', 'searching', 'extracting', 'validating'];
                                            const isProcessing = processingStatuses.includes(product.status);

                                            if (isProcessing) {
                                                return (
                                                    <div className="flex items-center justify-center">
                                                        <Loader2 className="w-4 h-4 animate-spin text-blue-400" />
                                                    </div>
                                                );
                                            }

                                            return (
                                                <Checkbox
                                                    className="border-zinc-700"
                                                    checked={selectedIds.has(product.id)}
                                                    onCheckedChange={(checked) => handleSelectOne(product.id, !!checked)}
                                                />
                                            );
                                        })()}
                                    </TableCell>
                                    <TableCell>
                                        {(() => {
                                            const processingStatuses = ['enriching', 'classifying', 'searching', 'extracting', 'validating'];
                                            const isActive = processingStatuses.includes(product.status);
                                            const statusColors: Record<string, string> = {
                                                done: 'bg-emerald-500/10 text-emerald-500',
                                                error: 'bg-red-500/10 text-red-500',
                                                needs_review: 'bg-yellow-500/10 text-yellow-500',
                                                classifying: 'bg-purple-500/10 text-purple-400 animate-pulse ring-1 ring-purple-500/50',
                                                searching: 'bg-blue-500/10 text-blue-400 animate-pulse ring-1 ring-blue-500/50',
                                                extracting: 'bg-orange-500/10 text-orange-400 animate-pulse ring-1 ring-orange-500/50',
                                                validating: 'bg-green-500/10 text-green-400 animate-pulse ring-1 ring-green-500/50',
                                                enriching: 'bg-indigo-500/10 text-indigo-400 animate-pulse ring-1 ring-indigo-500/50',
                                                pending: 'bg-zinc-800 text-zinc-500',
                                            };
                                            const color = statusColors[product.status] || 'bg-zinc-800 text-zinc-500';
                                            return (
                                                <Badge variant="outline" className={`border-0 uppercase text-[10px] tracking-wider font-bold px-2 py-0.5 ${color}`}>
                                                    {product.status === 'done' && <CheckCircle2 className="w-3 h-3 mr-1" />}
                                                    {product.status === 'needs_review' && <AlertTriangle className="w-3 h-3 mr-1" />}
                                                    {product.status === 'error' && <AlertCircle className="w-3 h-3 mr-1" />}
                                                    {isActive && <Loader2 className="w-3 h-3 mr-1 animate-spin" />}
                                                    {(product.status || 'pending').replace('_', ' ')}
                                                </Badge>
                                            );
                                        })()}
                                    </TableCell>
                                    <TableCell>
                                        <div className="flex flex-col gap-1 max-w-[280px]">
                                            <span className={`font-medium truncate ${product.status === 'enriching' ? 'text-blue-200' : 'text-zinc-200'}`}>{product.product_name}</span>
                                            <span className="text-[10px] font-mono text-zinc-500">{product.ean}</span>
                                        </div>
                                    </TableCell>
                                    <TableCell>
                                        {getClassification(product)}
                                    </TableCell>
                                    <TableCell className="text-center">
                                        <div className="flex flex-col items-center gap-1 text-xs">
                                            {/* Combine L x W x H nicely */}
                                            <div className="flex items-center gap-1">
                                                <span className="text-zinc-500">L:</span> {getEnrichedVal(product, 'length')}
                                            </div>
                                            <div className="flex items-center gap-1">
                                                <span className="text-zinc-500">W:</span> {getEnrichedVal(product, 'width')}
                                            </div>
                                            <div className="flex items-center gap-1">
                                                <span className="text-zinc-500">H:</span> {getEnrichedVal(product, 'height')}
                                            </div>
                                        </div>
                                    </TableCell>
                                    <TableCell className="text-center">
                                        {getEnrichedVal(product, 'weight')}
                                    </TableCell>
                                    <TableCell className="text-center">
                                        {getEnrichedVal(product, 'color')}
                                    </TableCell>
                                    <TableCell className="text-center">
                                        {getEnrichedVal(product, 'country_of_origin')}
                                    </TableCell>
                                    <TableCell>
                                        <Button variant="ghost" size="icon" className="h-8 w-8 text-zinc-500 hover:text-white opacity-0 group-hover:opacity-100 transition-opacity">
                                            <ChevronRight className="w-4 h-4" />
                                        </Button>
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </div>
            </div>
            {/* Footer */}
            <div className="flex items-center justify-between text-[10px] text-zinc-600 uppercase tracking-widest px-1">
                <div>Phase 5: Polish & Scale</div>
                <div>v1.0.0</div>
            </div>
        </div>
    );
}
