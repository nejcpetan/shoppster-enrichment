"use client";

import { useState } from "react";
import { fetchAPI } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Upload, Loader2, Sparkles, FileText, CheckCircle2 } from "lucide-react";

export function UploadCSV({ onUploadSuccess }: { onUploadSuccess: () => void }) {
    const [file, setFile] = useState<File | null>(null);
    const [uploading, setUploading] = useState(false);
    const [message, setMessage] = useState<string | null>(null);
    const [isDragOver, setIsDragOver] = useState(false);

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files[0]) {
            setFile(e.target.files[0]);
            setMessage(null);
        }
    };

    const handleDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(false);
        if (e.dataTransfer.files && e.dataTransfer.files[0]) {
            setFile(e.dataTransfer.files[0]);
            setMessage(null);
        }
    };

    const handleDragOver = (e: React.DragEvent) => {
        e.preventDefault();
        setIsDragOver(true);
    };

    const handleDragLeave = () => {
        setIsDragOver(false);
    };

    const handleUpload = async () => {
        if (!file) return;

        setUploading(true);
        setMessage(null);

        const formData = new FormData();
        formData.append("file", file);

        try {
            const res = await fetch("http://localhost:8000/api/upload", {
                method: "POST",
                body: formData,
            });

            if (!res.ok) throw new Error("Upload failed");

            const data = await res.json();
            setMessage(data.message || "Upload successful!");
            onUploadSuccess();
            // Reset after success
            setTimeout(() => {
                setFile(null);
                setMessage(null);
            }, 3000);

        } catch (error) {
            console.error(error);
            setMessage("Failed to upload file.");
        } finally {
            setUploading(false);
        }
    };

    return (
        <Card className={`w-full bg-zinc-900/50 border border-dashed transition-all duration-300 relative overflow-hidden group rounded-lg
        ${isDragOver ? "border-zinc-500 bg-zinc-900" : "border-zinc-800 hover:border-zinc-700"}
        ${file ? "border-solid border-zinc-700" : ""}
    `}>
            <CardContent className="p-0">
                <div
                    className="flex items-center justify-between p-4 min-h-[80px]"
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                >
                    {/* Left Side: Icon & Text */}
                    <div className="flex items-center gap-4 flex-1">
                        <div className={`w-10 h-10 rounded-full flex items-center justify-center border transition-all duration-500
                ${uploading ? "bg-zinc-800 border-zinc-700" :
                                file ? "bg-zinc-100 border-zinc-200 text-black shadow-sm" : "bg-zinc-900 border-zinc-800 text-zinc-600"}
             `}>
                            {uploading ? (
                                <Loader2 className="w-5 h-5 animate-spin text-zinc-400" />
                            ) : message === "Upload successful!" ? (
                                <CheckCircle2 className="w-5 h-5 text-emerald-600" />
                            ) : file ? (
                                <FileText className="w-5 h-5 text-black fill-zinc-200/50" />
                            ) : (
                                <Upload className="w-5 h-5" />
                            )}
                        </div>

                        <div className="space-y-0.5">
                            <h3 className="text-sm font-medium text-zinc-200">
                                {uploading ? "Uploading..." :
                                    message === "Upload successful!" ? "Import Complete" :
                                        file ? file.name : "Upload Product Catalog"}
                            </h3>
                            <p className="text-xs text-zinc-500">
                                {uploading ? "Parsing CSV data..." :
                                    message === "Upload successful!" ? "Data is being processed in the background." :
                                        file ? `${(file.size / 1024).toFixed(0)} KB • Ready to upload` : "Drag drop or click to browse CSV"}
                            </p>
                        </div>
                    </div>

                    {/* Right Side: Action Button */}
                    <div className="relative z-10">
                        {file && !message && (
                            <Button
                                onClick={handleUpload}
                                disabled={uploading}
                                size="sm"
                                className="bg-zinc-100 hover:bg-white text-zinc-950 shadow-sm font-medium border border-transparent"
                            >
                                {uploading ? "Processing" : "Start Import"}
                                {!uploading && <Sparkles className="w-3 h-3 ml-2 fill-current" />}
                            </Button>
                        )}

                        {!file && (
                            <div className="relative">
                                <Input
                                    type="file"
                                    accept=".csv,.xlsx"
                                    onChange={handleFileChange}
                                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                    disabled={uploading}
                                />
                                <Button variant="outline" size="sm" className="border-zinc-700 text-zinc-400 hover:text-white hover:bg-zinc-800 pointer-events-none">
                                    Browse Files
                                </Button>
                            </div>
                        )}
                    </div>

                    {/* Success Message Overlay or Animation can go here if needed, but keeping it inline for compactness */}
                </div>

                {/* Progress Bar (Visual only for now) */}
                {uploading && (
                    <div className="absolute bottom-0 left-0 h-0.5 bg-zinc-200 animate-pulse w-full"></div>
                )}
            </CardContent>
        </Card>
    );
}
