import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { X, LoaderCircle, AlertCircle } from "lucide-react";
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const el = ref.current;
    el?.showModal();
    return () => el?.close();
  }, []);
  return (
    <dialog
      ref={ref}
      className={"modal " + (wide ? "wide" : "")}
      aria-labelledby={id}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-heading">
        <h2 id={id}>{title}</h2>
        <button
          className="icon-button"
          aria-label="Close dialog"
          onClick={onClose}
        >
          <X size={19} />
        </button>
      </div>
      {children}
    </dialog>
  );
}
export function ErrorNotice({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="error" role="alert">
      <AlertCircle size={18} />
      <div>
        {message}
        {retry && (
          <button type="button" className="text-button" onClick={retry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={19} className="spin" />
      {children}
    </div>
  );
}
export function StatePill({ state }: { state?: string | null }) {
  const names: Record<string, string> = {
    completed: "Ready",
    synced: "Ready",
    retained: "Ready",
    pending: "Waiting",
    running: "In progress",
    processing: "In progress",
    failed: "Needs attention",
    retrying: "Retrying",
    uncertain: "Needs checking",
    waiting: "Waiting",
  };
  return (
    <span
      className={
        "pill " +
        (["completed", "synced", "retained"].includes(state || "")
          ? "ready"
          : state === "failed"
            ? "failed"
            : "")
      }
    >
      {names[state || ""] || "Not available"}
    </span>
  );
}
export const isFinished = (status?: string) =>
  ["completed", "failed", "cancelled"].includes(status || "");
export function dateLabel(date: string) {
  return new Date(date).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
export function batchLabel(id?: string) {
  if (!id) return "All selected reviews";
  const end = id.split(":").at(-1);
  return end && ["A", "B", "C"].includes(end) ? `Group ${end}` : id;
}
