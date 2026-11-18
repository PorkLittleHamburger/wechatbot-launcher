import { FEEDBACK_DISMISS_MS } from '../operation-feedback';

export function Notice({ text, sequence, onClose }: { text: string; sequence: number; onClose: () => void }) {
  return <div className="alert info notice" role="status">
    <span>{text}</span>
    <button onClick={onClose} aria-label="关闭消息">×</button>
    <span key={sequence} className="notice-countdown" data-countdown="notice"
      style={{ animationDuration: `${FEEDBACK_DISMISS_MS}ms` }} aria-hidden="true" />
  </div>;
}
