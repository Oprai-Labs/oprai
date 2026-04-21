"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AdminHeader } from "@/components/layout/admin-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ToastContainer } from "@/components/ui/toast";
import { fetchSessionMessages, closeSession as apiCloseSession } from "@/lib/admin-api";
import { formatDate } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";
import { XCircle, ArrowDown } from "lucide-react";
import { useRef } from "react";

export default function SessionDetailPage() {
  const params = useParams();
  const sessionId = params.id as string;
  const { toasts, dismissToast, success, error } = useToast();
  const bottomRef = useRef<HTMLDivElement>(null);

  const [messages, setMessages] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    fetchSessionMessages(sessionId)
      .then((res) => setMessages(res.messages || []))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [sessionId]);

  const handleClose = async () => {
    try {
      await apiCloseSession(sessionId);
      success("Session closed");
    } catch (err: unknown) {
      error(err instanceof Error ? err.message : "Failed to close session");
    }
  };

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const filteredMessages = searchQuery
    ? messages.filter((m) => m.content?.toLowerCase().includes(searchQuery.toLowerCase()))
    : messages;

  return (
    <div>
      <AdminHeader
        title="Session Messages"
        description={`Session: ${sessionId}`}
        breadcrumbs={[
          { label: "Sessions", href: "/sessions" },
          { label: sessionId.slice(0, 8) + "..." },
        ]}
        actions={
          <Button variant="destructive" size="sm" onClick={handleClose} className="gap-2">
            <XCircle className="h-4 w-4" /> Close Session
          </Button>
        }
      />
      <div className="space-y-4 p-8">
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Search within messages..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="h-9 w-72 rounded-lg border border-line bg-bg-elevated px-3 text-sm text-fg placeholder:text-fg-subtle focus:border-accent/50 focus:outline-none focus:ring-2 focus:ring-accent/20"
          />
          <span className="text-xs text-fg-muted">
            {filteredMessages.length} message{filteredMessages.length !== 1 ? "s" : ""}
          </span>
        </div>

        <Card>
          <CardContent className="p-6">
            {loading ? (
              <div className="space-y-4">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-3/4" />
                ))}
              </div>
            ) : filteredMessages.length === 0 ? (
              <div className="text-center text-fg-muted">No messages found</div>
            ) : (
              <div className="space-y-4">
                {filteredMessages.map((msg, idx) => {
                  const isUser = msg.role === "user";
                  const isSystem = msg.role === "system";

                  if (isSystem) {
                    return (
                      <div key={msg.id || idx} className="flex justify-center">
                        <div className="rounded-lg bg-bg-hover px-3 py-1.5 text-xs text-fg-muted">
                          {msg.content}
                        </div>
                      </div>
                    );
                  }

                  return (
                    <div
                      key={msg.id || idx}
                      className={cn("flex", isUser ? "justify-end" : "justify-start")}
                    >
                      <div
                        className={cn(
                          "max-w-[70%] rounded-xl px-4 py-3",
                          isUser
                            ? "bg-accent/20 text-fg"
                            : "bg-bg-hover text-fg"
                        )}
                      >
                        <div className="mb-1 flex items-center gap-2">
                          <Badge variant={isUser ? "info" : "default"}>
                            {isUser ? "User" : "Assistant"}
                          </Badge>
                          <span className="text-xs text-fg-subtle">
                            {formatDate(msg.created_at)}
                          </span>
                        </div>
                        <p className="whitespace-pre-wrap text-sm">{msg.content}</p>
                        {msg.token_usage != null && (
                          <p className="mt-1 text-xs text-fg-subtle">
                            Tokens: {msg.token_usage}
                          </p>
                        )}
                      </div>
                    </div>
                  );
                })}
                <div ref={bottomRef} />
              </div>
            )}
          </CardContent>
        </Card>

        {messages.length > 10 && (
          <div className="fixed bottom-8 right-8">
            <Button variant="secondary" size="icon" onClick={scrollToBottom}>
              <ArrowDown className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
