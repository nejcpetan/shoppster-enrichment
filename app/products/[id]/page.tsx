"use client";

import { use } from "react";
import { ProductDetail } from "@/components/ProductDetail";

export default function ProductPage({ params }: { params: Promise<{ id: string }> }) {
    const { id } = use(params);

    return (
        <div className="min-h-screen bg-black text-zinc-100 font-sans selection:bg-purple-500/30">
            <div className="max-w-7xl mx-auto px-6 py-8">
                <ProductDetail productId={parseInt(id)} />
            </div>
        </div>
    );
}
