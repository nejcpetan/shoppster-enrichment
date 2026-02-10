"use client";

import { useEffect, useState } from "react";
import { fetchAPI } from "@/lib/api";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import Link from "next/link";
import {
    ArrowLeft, Search, ExternalLink, Download, CheckCircle2, AlertTriangle,
    Play, Loader2, Box, Database, Ruler, Scale, Palette, Globe2, Check,
    RotateCcw, Clock, Zap, XCircle, ChevronDown, ChevronUp, Maximize2,
    Brain, FileText, ShieldCheck, Image as ImageIcon
} from "lucide-react";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

interface ProductDetailProps {
    productId: number;
}

// Map of statuses to colors
const statusConfig: Record<string, { color: string; label: string }> = {
    pending: { color: "bg-zinc-500/10 text-zinc-400 border-zinc-500/20", label: "Pending" },
    enriching: { color: "bg-indigo-500/10 text-indigo-400 border-indigo-500/20", label: "Enriching" },
    classifying: { color: "bg-purple-500/10 text-purple-400 border-purple-500/20", label: "Classifying" },
    searching: { color: "bg-blue-500/10 text-blue-400 border-blue-500/20", label: "Searching" },
    extracting: { color: "bg-orange-500/10 text-orange-400 border-orange-500/20", label: "Extracting" },
    validating: { color: "bg-green-500/10 text-green-400 border-green-500/20", label: "Validating" },
    done: { color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20", label: "Done" },
    needs_review: { color: "bg-yellow-500/10 text-yellow-400 border-yellow-500/20", label: "Needs Review" },
    error: { color: "bg-red-500/10 text-red-400 border-red-500/20", label: "Error" },
};

const PHASES = [
    { key: 'classifying', label: 'Classify', icon: Brain, color: 'purple' },
    { key: 'searching', label: 'Search', icon: Search, color: 'blue' },
    { key: 'extracting', label: 'Extract', icon: FileText, color: 'orange' },
    { key: 'validating', label: 'Validate', icon: ShieldCheck, color: 'green' },
];

const PROCESSING_STATUSES = ['classifying', 'searching', 'extracting', 'validating', 'enriching'];

function getPhaseIndex(status: string): number {
    const idx = PHASES.findIndex(p => p.key === status);
    if (status === 'enriching') return 0;
    if (status === 'done' || status === 'needs_review') return 4;
    return idx;
}

export function ProductDetail({ productId }: ProductDetailProps) {
    const [product, setProduct] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [enriching, setEnriching] = useState(false);
    const [searching, setSearching] = useState(false);
    const [extracting, setExtracting] = useState(false);
    const [validating, setValidating] = useState(false);
    const [classifying, setClassifying] = useState(false);
    const [resetting, setResetting] = useState(false);
    const [logExpanded, setLogExpanded] = useState(false);
    const logEndRef = useState<HTMLDivElement | null>(null);
    const [imageModalOpen, setImageModalOpen] = useState(false);
    const [selectedImage, setSelectedImage] = useState<string | null>(null);

    useEffect(() => {
        loadProduct();
    }, [productId]);

    // Auto-refresh when product is in a processing state — 2s polling
    useEffect(() => {
        if (!product) return;
        if (PROCESSING_STATUSES.includes(product.status)) {
            const interval = setInterval(() => loadProduct(), 2000);
            return () => clearInterval(interval);
        }
    }, [product?.status]);

    // Auto-expand enrichment log while processing
    useEffect(() => {
        if (product && PROCESSING_STATUSES.includes(product.status)) {
            setLogExpanded(true);
        }
    }, [product?.status]);

    const loadProduct = async () => {
        try {
            const data = await fetchAPI(`/products/${productId}`);
            setProduct(data);
            if (data.status !== 'classifying') setClassifying(false);
            if (data.status !== 'searching') setSearching(false);
            if (data.status !== 'extracting') setExtracting(false);
            if (data.status !== 'validating') setValidating(false);
            if (!PROCESSING_STATUSES.includes(data.status)) {
                setEnriching(false);
            }
        } catch (error) {
            console.error("Failed to load product", error);
        } finally {
            setLoading(false);
        }
    };

    const runClassification = async () => {
        setClassifying(true);
        try {
            await fetchAPI(`/products/${productId}/classify`, { method: "POST" });
            setTimeout(loadProduct, 2000);
        } catch (error) {
            console.error("Classification failed", error);
            setClassifying(false);
        }
    };

    const runFullEnrichment = async () => {
        setEnriching(true);
        try {
            await fetchAPI(`/products/${productId}/enrich`, { method: "POST" });
            setTimeout(loadProduct, 2000);
        } catch (error) {
            console.error("Enrichment failed", error);
            setEnriching(false);
        }
    };

    const runSearch = async () => {
        setSearching(true);
        try {
            await fetchAPI(`/products/${productId}/search`, { method: "POST" });
            setTimeout(loadProduct, 2000);
        } catch (error) {
            console.error("Search failed", error);
            setSearching(false);
        }
    }

    const runExtraction = async () => {
        setExtracting(true);
        try {
            await fetchAPI(`/products/${productId}/extract`, { method: "POST" });
            setTimeout(loadProduct, 2000);
        } catch (error) {
            console.error("Extraction failed", error);
            setExtracting(false);
        }
    }

    const runValidation = async () => {
        setValidating(true);
        try {
            await fetchAPI(`/products/${productId}/validate`, { method: "POST" });
            setTimeout(loadProduct, 2000);
        } catch (error) {
            console.error("Validation failed", error);
            setValidating(false);
        }
    }

    const resetProduct = async () => {
        setResetting(true);
        try {
            await fetchAPI(`/products/${productId}/reset`, { method: "POST" });
            await loadProduct();
        } catch (error) {
            console.error("Reset failed", error);
        } finally {
            setResetting(false);
        }
    }

    const exportProduct = () => {
        window.open(`http://localhost:8000/api/products/${productId}/export`, '_blank');
    }

    if (loading && !product) return (
        <div className="flex items-center justify-center h-64">
            <div className="flex flex-col items-center gap-3">
                <Loader2 className="w-8 h-8 animate-spin text-blue-400" />
                <span className="text-zinc-500 text-sm">Loading product...</span>
            </div>
        </div>
    );
    if (!product) return <div>Product not found</div>;

    const classification = product.classification_result ? JSON.parse(product.classification_result) : null;
    const searchResults = product.search_result ? JSON.parse(product.search_result) : null;
    const extraction = product.extraction_result ? JSON.parse(product.extraction_result) : null;
    const validation = product.validation_result ? JSON.parse(product.validation_result) : null;
    const enrichmentLog = product.enrichment_log ? JSON.parse(product.enrichment_log) : [];

    const displayData = validation ? validation.normalized_data : extraction;

    const isProcessing = PROCESSING_STATUSES.includes(product.status);
    const currentStatus = statusConfig[product.status] || statusConfig.pending;
    const phaseIdx = getPhaseIndex(product.status);

    // Collect all image URLs
    const allImages: string[] = [];
    if (displayData?.image_urls) allImages.push(...displayData.image_urls);
    if (displayData?.image_url?.value && !allImages.includes(displayData.image_url.value)) {
        allImages.unshift(displayData.image_url.value);
    }

    // Helper to render extraction rows
    const renderExtractionRow = (label: string, field: any, icon: React.ReactNode) => {
        if (!field || field.value === null) return null;
        return (
            <TableRow className="border-zinc-800/50 hover:bg-zinc-800/30">
                <TableCell className="font-medium text-zinc-400 flex items-center gap-2">
                    {icon} {label}
                </TableCell>
                <TableCell className="font-mono text-zinc-200">
                    {field.value} {field.unit}
                </TableCell>
                <TableCell>
                    {field.confidence === 'official' && <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 text-[10px] uppercase">Official</Badge>}
                    {field.confidence === 'third_party' && <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 text-[10px] uppercase">Third Party</Badge>}
                    {field.confidence === 'inferred' && <Badge className="bg-orange-500/10 text-orange-400 border-orange-500/20 text-[10px] uppercase">Inferred</Badge>}
                    {field.dimension_type && field.dimension_type !== 'na' && (
                        <Badge className="ml-1 bg-zinc-800 text-zinc-400 border-zinc-700 text-[9px] uppercase">{field.dimension_type}</Badge>
                    )}
                </TableCell>
                <TableCell className="text-right">
                    {field.source_url && (
                        <a href={field.source_url} target="_blank" rel="noreferrer" className="text-blue-400 hover:text-blue-300">
                            <ExternalLink className="w-3 h-3 ml-auto" />
                        </a>
                    )}
                </TableCell>
            </TableRow>
        );
    };

    // Log entry icon
    const getLogIcon = (entry: any) => {
        if (entry.status === 'error') return <XCircle className="w-3 h-3 text-red-400 shrink-0" />;
        if (entry.status === 'warning') return <AlertTriangle className="w-3 h-3 text-yellow-400 shrink-0" />;
        if (entry.status === 'success') return <CheckCircle2 className="w-3 h-3 text-emerald-400 shrink-0" />;
        return <Clock className="w-3 h-3 text-zinc-500 shrink-0" />;
    };

    const phaseColors: Record<string, string> = {
        triage: "text-purple-400",
        search: "text-blue-400",
        extract: "text-orange-400",
        validate: "text-green-400",
        pipeline: "text-zinc-400",
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <div className="mb-6">
                        <Link href="/">
                            <Button variant="ghost" className="text-zinc-400 hover:text-white pl-0 hover:bg-transparent">
                                <ArrowLeft className="w-4 h-4 mr-2" />
                                Back to Dashboard
                            </Button>
                        </Link>
                    </div>
                    <h1 className="text-2xl font-bold text-zinc-100">{product.product_name}</h1>
                    <div className="flex items-center gap-2 mt-2 text-zinc-500 font-mono text-sm">
                        <span className="bg-zinc-900 px-2 py-1 rounded border border-zinc-800">ID: {product.id}</span>
                        <span className="bg-zinc-900 px-2 py-1 rounded border border-zinc-800">EAN: {product.ean}</span>
                        <Badge className={`${currentStatus.color} px-2 py-0.5`}>
                            {isProcessing && <Loader2 className="w-3 h-3 mr-1 animate-spin" />}
                            {currentStatus.label}
                        </Badge>
                    </div>
                </div>
                {/* Global Actions */}
                <div className="flex gap-2">
                    <Button
                        size="sm"
                        variant="outline"
                        className="border-zinc-700 text-zinc-400 hover:text-white hover:bg-zinc-800"
                        onClick={resetProduct}
                        disabled={resetting || isProcessing}
                    >
                        {resetting ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4 mr-1" />}
                        Reset
                    </Button>
                    <Button
                        size="sm"
                        variant="outline"
                        className="border-zinc-700 text-zinc-400 hover:text-white hover:bg-zinc-800"
                        onClick={exportProduct}
                    >
                        <Download className="w-4 h-4 mr-1" />
                        Export
                    </Button>
                    <Button
                        size="sm"
                        className={`
                            relative overflow-hidden transition-all duration-300 shadow-lg font-medium tracking-wide
                            ${enriching || isProcessing
                                ? 'bg-zinc-900 text-zinc-500 cursor-not-allowed border border-zinc-800'
                                : 'bg-zinc-100 hover:bg-white text-zinc-950 border border-transparent shadow-zinc-500/10'}
                        `}
                        onClick={runFullEnrichment}
                        disabled={enriching || isProcessing}
                    >
                        {enriching || isProcessing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Zap className="w-4 h-4 mr-1 fill-current" />}
                        Run Full Pipeline
                    </Button>
                </div>
            </div>

            {/* Live Agent Progress Banner */}
            {isProcessing && (
                <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 backdrop-blur-sm p-4 space-y-3">
                    {/* Pipeline Progress */}
                    <div className="flex items-center gap-2">
                        {PHASES.map((phase, i) => {
                            const isDone = i < phaseIdx;
                            const isCurrent = i === phaseIdx;
                            const PhaseIcon = phase.icon;
                            const colorClasses: Record<string, { active: string, done: string, pending: string }> = {
                                purple: { active: 'bg-purple-500/20 border-purple-500 text-purple-400', done: 'bg-purple-500/10 border-purple-500/30 text-purple-400', pending: 'bg-zinc-900 border-zinc-700 text-zinc-600' },
                                blue: { active: 'bg-blue-500/20 border-blue-500 text-blue-400', done: 'bg-blue-500/10 border-blue-500/30 text-blue-400', pending: 'bg-zinc-900 border-zinc-700 text-zinc-600' },
                                orange: { active: 'bg-orange-500/20 border-orange-500 text-orange-400', done: 'bg-orange-500/10 border-orange-500/30 text-orange-400', pending: 'bg-zinc-900 border-zinc-700 text-zinc-600' },
                                green: { active: 'bg-green-500/20 border-green-500 text-green-400', done: 'bg-green-500/10 border-green-500/30 text-green-400', pending: 'bg-zinc-900 border-zinc-700 text-zinc-600' },
                            };
                            const cc = colorClasses[phase.color];
                            const cls = isCurrent ? cc.active : isDone ? cc.done : cc.pending;

                            return (
                                <div key={phase.key} className="flex items-center gap-2 flex-1">
                                    <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-bold uppercase tracking-wider transition-all ${cls} ${isCurrent ? 'ring-1 ring-offset-1 ring-offset-black' : ''}`}>
                                        <PhaseIcon className={`w-3.5 h-3.5 ${isCurrent ? 'animate-pulse' : ''}`} />
                                        {phase.label}
                                        {isDone && <CheckCircle2 className="w-3 h-3" />}
                                        {isCurrent && <Loader2 className="w-3 h-3 animate-spin" />}
                                    </div>
                                    {i < PHASES.length - 1 && (
                                        <div className={`flex-1 h-px ${isDone ? 'bg-zinc-600' : 'bg-zinc-800'}`} />
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    {/* Current Step Message */}
                    {product.current_step && (
                        <div className="flex items-center gap-2 text-sm">
                            <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-400 shrink-0" />
                            <span className="text-zinc-400 font-mono text-xs">{product.current_step}</span>
                        </div>
                    )}
                </div>
            )}

            {/* Phase Cards Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Left Column: Pipeline Steps */}
                <div className="space-y-6">
                    {/* Phase 1 */}
                    <Card className={`border-zinc-800 backdrop-blur-sm ${classification ? 'bg-zinc-900/50' : 'bg-transparent border-dashed'}`}>
                        <CardHeader className="flex flex-row items-center justify-between pb-2 border-b border-zinc-800/50">
                            <div className="space-y-1">
                                <CardTitle className="text-sm uppercase tracking-widest text-zinc-400 flex items-center gap-2"><Brain className="w-4 h-4 text-purple-400" /> Classification</CardTitle>
                            </div>
                            {classification ? <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 px-2 py-0.5"><CheckCircle2 className="w-3 h-3 mr-1" /> Done</Badge> :
                                product.status === 'classifying' ? <Badge className="bg-purple-500/10 text-purple-400 border-purple-500/20 px-2 py-0.5"><Loader2 className="w-3 h-3 mr-1 animate-spin" /> Running</Badge> :
                                    <Button size="sm" onClick={runClassification} disabled={classifying || isProcessing} className="bg-purple-600 hover:bg-purple-500 text-white h-7 text-xs">{classifying ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}</Button>}
                        </CardHeader>
                        {classification && (
                            <CardContent className="pt-4 space-y-2">
                                <div className="flex items-center justify-between">
                                    <span className="text-zinc-200 font-medium">{classification.product_type}</span>
                                    <span className="text-zinc-500 text-sm">{classification.brand || 'Unknown brand'}</span>
                                </div>
                                {classification.model_number && (
                                    <div className="text-xs text-zinc-500 font-mono">Model: {classification.model_number}</div>
                                )}
                                {classification.parsed_color && (
                                    <div className="text-xs text-zinc-500">Color: {classification.parsed_color}</div>
                                )}
                                <div className="text-xs text-zinc-600 mt-1">{classification.reasoning}</div>
                            </CardContent>
                        )}
                    </Card>

                    {/* Phase 2 */}
                    <Card className={`border-zinc-800 backdrop-blur-sm ${searchResults ? 'bg-zinc-900/50' : 'bg-transparent border-dashed'}`}>
                        <CardHeader className="flex flex-row items-center justify-between pb-2 border-b border-zinc-800/50">
                            <div className="space-y-1">
                                <CardTitle className="text-sm uppercase tracking-widest text-zinc-400 flex items-center gap-2"><Search className="w-4 h-4 text-blue-400" /> Search</CardTitle>
                            </div>
                            {searchResults ? <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 px-2 py-0.5"><CheckCircle2 className="w-3 h-3 mr-1" /> {searchResults.results?.length || 0} URLs</Badge> :
                                product.status === 'searching' ? <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 px-2 py-0.5"><Loader2 className="w-3 h-3 mr-1 animate-spin" /> Searching</Badge> :
                                    <Button size="sm" onClick={runSearch} disabled={searching || !classification || isProcessing} className="bg-blue-600 hover:bg-blue-500 text-white h-7 text-xs">{searching ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}</Button>}
                        </CardHeader>
                        {searchResults && searchResults.results?.length > 0 && (
                            <CardContent className="pt-3 space-y-1">
                                {searchResults.results.map((r: any, i: number) => (
                                    <div key={i} className="flex items-center gap-2 text-xs py-1 border-b border-zinc-800/30 last:border-0">
                                        <Badge className={`text-[9px] px-1 shrink-0 ${r.source_type === 'manufacturer' ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' :
                                            r.source_type === 'authorized_distributor' ? 'bg-blue-500/10 text-blue-400 border-blue-500/20' :
                                                'bg-zinc-800 text-zinc-400 border-zinc-700'
                                            }`}>{r.source_type?.replace('_', ' ')}</Badge>
                                        <a href={r.url} target="_blank" rel="noreferrer" className="text-zinc-300 hover:text-white truncate">
                                            {r.title || new URL(r.url).hostname}
                                        </a>
                                        <ExternalLink className="w-3 h-3 text-zinc-600 shrink-0" />
                                    </div>
                                ))}
                            </CardContent>
                        )}
                    </Card>

                    {/* Phase 3 */}
                    <Card className={`border-zinc-800 backdrop-blur-sm ${extraction ? 'bg-zinc-900/50' : 'bg-transparent border-dashed'}`}>
                        <CardHeader className="flex flex-row items-center justify-between pb-2 border-b border-zinc-800/50">
                            <div className="space-y-1">
                                <CardTitle className="text-sm uppercase tracking-widest text-zinc-400 flex items-center gap-2"><Database className="w-4 h-4 text-orange-400" /> Extraction</CardTitle>
                            </div>
                            {extraction ? <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 px-2 py-0.5"><CheckCircle2 className="w-3 h-3 mr-1" /> Done</Badge> :
                                product.status === 'extracting' ? <Badge className="bg-orange-500/10 text-orange-400 border-orange-500/20 px-2 py-0.5"><Loader2 className="w-3 h-3 mr-1 animate-spin" /> Extracting</Badge> :
                                    <Button size="sm" onClick={runExtraction} disabled={extracting || !searchResults || isProcessing} className="bg-orange-600 hover:bg-orange-500 text-white h-7 text-xs">{extracting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}</Button>}
                        </CardHeader>
                    </Card>

                    {/* Phase 4 */}
                    <Card className={`border-zinc-800 backdrop-blur-sm ${validation ? 'bg-zinc-900/50' : 'bg-transparent border-dashed'}`}>
                        <CardHeader className="flex flex-row items-center justify-between pb-2 border-b border-zinc-800/50">
                            <div className="space-y-1">
                                <CardTitle className="text-sm uppercase tracking-widest text-zinc-400 flex items-center gap-2"><Check className="w-4 h-4 text-green-400" /> Validation</CardTitle>
                            </div>
                            {validation ? <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 px-2 py-0.5"><CheckCircle2 className="w-3 h-3 mr-1" /> Done</Badge> :
                                product.status === 'validating' ? <Badge className="bg-green-500/10 text-green-400 border-green-500/20 px-2 py-0.5"><Loader2 className="w-3 h-3 mr-1 animate-spin" /> Validating</Badge> :
                                    <Button size="sm" onClick={runValidation} disabled={validating || !extraction || isProcessing} className="bg-green-600 hover:bg-green-500 text-white h-7 text-xs">{validating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}</Button>}
                        </CardHeader>
                        {validation && (
                            <CardContent className="pt-4 space-y-2">
                                <div className="flex items-center justify-between">
                                    <span className="text-sm text-zinc-400">Quality Score</span>
                                    <Badge className={`${validation.report.overall_quality === 'good' ? 'bg-green-500/10 text-green-400' : validation.report.overall_quality === 'acceptable' ? 'bg-blue-500/10 text-blue-400' : 'bg-yellow-500/10 text-yellow-400'}`}>
                                        {validation.report.overall_quality.replace('_', ' ')}
                                    </Badge>
                                </div>
                                {validation.report.review_reason && (
                                    <div className="text-xs text-yellow-300/80 mt-1 p-2 bg-yellow-500/5 rounded border border-yellow-500/10">
                                        {validation.report.review_reason}
                                    </div>
                                )}
                                {validation.report.issues.length > 0 && (
                                    <div className="mt-2 p-2 bg-yellow-500/10 border border-yellow-500/20 rounded text-xs text-yellow-200">
                                        <div className="font-bold flex items-center gap-1"><AlertTriangle className="w-3 h-3" /> Issues Found:</div>
                                        <ul className="list-disc list-inside mt-1">
                                            {validation.report.issues.map((i: any, k: number) => (
                                                <li key={k} className={i.severity === 'error' ? 'text-red-300' : ''}>
                                                    <span className="font-mono">{i.field}</span>: {i.issue}
                                                    {i.severity === 'error' && <Badge className="ml-1 bg-red-500/10 text-red-400 border-red-500/20 text-[9px]">ERROR</Badge>}
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </CardContent>
                        )}
                    </Card>

                    {/* Enrichment Log Timeline */}
                    {enrichmentLog.length > 0 && (
                        <Card className="border-zinc-800 bg-zinc-900/30 backdrop-blur-sm">
                            <CardHeader className="pb-2 cursor-pointer" onClick={() => setLogExpanded(!logExpanded)}>
                                <div className="flex items-center justify-between">
                                    <CardTitle className="text-sm uppercase tracking-widest text-zinc-400 flex items-center gap-2">
                                        <Clock className="w-4 h-4 text-zinc-500" /> Enrichment Log
                                        <Badge className="bg-zinc-800 text-zinc-400 border-zinc-700 text-[10px]">{enrichmentLog.length}</Badge>
                                    </CardTitle>
                                    {logExpanded ? <ChevronUp className="w-4 h-4 text-zinc-500" /> : <ChevronDown className="w-4 h-4 text-zinc-500" />}
                                </div>
                            </CardHeader>
                            {logExpanded && (
                                <CardContent className="pt-0">
                                    <div className="space-y-0 relative">
                                        <div className="absolute left-[7px] top-2 bottom-2 w-px bg-zinc-800" />
                                        {enrichmentLog.map((entry: any, i: number) => (
                                            <div key={i} className="flex items-start gap-3 py-1.5 relative">
                                                <div className="z-10 bg-zinc-900">
                                                    {getLogIcon(entry)}
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center gap-2">
                                                        <span className={`text-[10px] font-bold uppercase tracking-wider ${phaseColors[entry.phase] || 'text-zinc-500'}`}>
                                                            {entry.phase}
                                                        </span>
                                                        {entry.step && (
                                                            <span className="text-[10px] text-zinc-600 font-mono">{entry.step}</span>
                                                        )}
                                                        {entry.credits_used && (
                                                            <span className="text-[10px] text-zinc-700">
                                                                {Object.entries(entry.credits_used).map(([k, v]) => `${k}:${v}`).join(' ')}
                                                            </span>
                                                        )}
                                                    </div>
                                                    <div className="text-xs text-zinc-400 truncate">{entry.details}</div>
                                                </div>
                                                <span className="text-[10px] text-zinc-700 shrink-0 font-mono">
                                                    {new Date(entry.timestamp).toLocaleTimeString()}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </CardContent>
                            )}
                        </Card>
                    )}
                </div>

                {/* Right Column: Golden Record + Images */}
                <div className="space-y-6">
                    <Card className={`border-zinc-800 bg-zinc-900/50 backdrop-blur-sm h-fit ${!displayData ? 'opacity-50' : ''}`}>
                        <CardHeader className="border-b border-zinc-800/50">
                            <CardTitle className="text-lg text-zinc-200">Golden Record</CardTitle>
                            <CardDescription>Final Enriched Data</CardDescription>
                        </CardHeader>
                        <CardContent className="p-0">
                            {displayData ? (
                                <Table>
                                    <TableHeader className="bg-zinc-950/50">
                                        <TableRow className="border-zinc-800 hover:bg-transparent">
                                            <TableHead className="text-[10px] uppercase font-bold text-zinc-500 pl-6">Field</TableHead>
                                            <TableHead className="text-[10px] uppercase font-bold text-zinc-500">Value</TableHead>
                                            <TableHead className="text-[10px] uppercase font-bold text-zinc-500">Type</TableHead>
                                            <TableHead className="text-[10px] uppercase font-bold text-zinc-500 text-right pr-6">Src</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {renderExtractionRow("Height", displayData.height, <Ruler className="w-3 h-3" />)}
                                        {renderExtractionRow("Length", displayData.length, <Ruler className="w-3 h-3" />)}
                                        {renderExtractionRow("Width", displayData.width, <Ruler className="w-3 h-3" />)}
                                        {renderExtractionRow("Weight", displayData.weight, <Scale className="w-3 h-3" />)}
                                        {renderExtractionRow("Volume", displayData.volume, <Database className="w-3 h-3" />)}
                                        {renderExtractionRow("Diameter", displayData.diameter, <Ruler className="w-3 h-3" />)}
                                        {renderExtractionRow("Thickness", displayData.thickness, <Ruler className="w-3 h-3" />)}
                                        {renderExtractionRow("Color", displayData.color, <Palette className="w-3 h-3" />)}
                                        {renderExtractionRow("Origin", displayData.country_of_origin, <Globe2 className="w-3 h-3" />)}
                                    </TableBody>
                                </Table>
                            ) : (
                                <div className="p-8 text-center text-zinc-500">
                                    <Database className="w-8 h-8 mx-auto mb-2 opacity-20" />
                                    <p>No data extracted yet.</p>
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    {/* Product Images Gallery */}
                    {allImages.length > 0 && (
                        <Card className="border-zinc-800 bg-zinc-900/50 backdrop-blur-sm">
                            <CardHeader className="border-b border-zinc-800/50">
                                <CardTitle className="text-sm uppercase tracking-widest text-zinc-400 flex items-center gap-2">
                                    <ImageIcon className="w-4 h-4 text-blue-400" /> Product Images
                                    <Badge className="bg-zinc-800 text-zinc-400 border-zinc-700 text-[10px]">{allImages.length}</Badge>
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="pt-4">
                                <div className="grid grid-cols-3 gap-2">
                                    {allImages.slice(0, 12).map((imgUrl, i) => (
                                        <div
                                            key={i}
                                            className="relative group cursor-pointer aspect-square rounded-lg border border-zinc-700 overflow-hidden bg-zinc-800 hover:border-blue-500 transition-colors"
                                            onClick={() => {
                                                setSelectedImage(imgUrl);
                                                setImageModalOpen(true);
                                            }}
                                        >
                                            <img
                                                src={`/api/image-proxy?url=${encodeURIComponent(imgUrl)}`}
                                                alt={`Product ${i + 1}`}
                                                className="w-full h-full object-contain p-1"
                                                loading="lazy"
                                            />
                                            {i === 0 && (
                                                <div className="absolute top-1 left-1">
                                                    <Badge className="bg-blue-600 text-white text-[8px] px-1 py-0">Primary</Badge>
                                                </div>
                                            )}
                                            <div className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition-colors flex items-center justify-center opacity-0 group-hover:opacity-100">
                                                <Maximize2 className="w-5 h-5 text-white" />
                                            </div>
                                        </div>
                                    ))}
                                </div>
                                {allImages.length > 12 && (
                                    <div className="text-center text-xs text-zinc-600 mt-2">
                                        +{allImages.length - 12} more images
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    )}
                </div>
            </div>

            {/* Image Modal */}
            <Dialog open={imageModalOpen} onOpenChange={setImageModalOpen}>
                <DialogContent className="max-w-4xl bg-zinc-900 border-zinc-800">
                    <DialogHeader>
                        <DialogTitle className="text-zinc-200">Product Image</DialogTitle>
                    </DialogHeader>
                    <div className="flex items-center justify-center p-4">
                        {selectedImage && (
                            <img
                                src={`/api/image-proxy?url=${encodeURIComponent(selectedImage)}`}
                                alt="Product - Full Size"
                                className="max-w-full max-h-[70vh] object-contain rounded border border-zinc-700"
                            />
                        )}
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
