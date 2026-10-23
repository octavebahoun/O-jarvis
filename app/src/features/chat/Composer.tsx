import { ArrowUp, Mic, Square } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

import styles from "./Chat.module.css";

interface VoiceControl {
  /** Jarvis écoute, réfléchit ou parle à voix haute. */
  active: boolean;
  onListen: () => void;
  onStop: () => void;
}

interface ComposerProps {
  busy: boolean;
  disabled: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  /** Absent quand la voix est coupée ou indisponible. */
  voice?: VoiceControl | undefined;
}

const MAX_ROWS_PX = 180;

export function Composer({ busy, disabled, onSend, onStop, voice }: ComposerProps) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const hintId = useId();

  useLayoutEffect(() => {
    const area = ref.current;
    if (!area) return;
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, MAX_ROWS_PX)}px`;
  }, [text]);

  const submit = () => {
    if (!text.trim() || busy || disabled) return;
    onSend(text);
    setText("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form
      className={`${styles.composer} glass3d`}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label htmlFor={`${hintId}-field`} className="visually-hidden">
        Message pour Jarvis
      </label>
      <textarea
        id={`${hintId}-field`}
        ref={ref}
        className={styles.field}
        rows={1}
        placeholder={disabled ? "Le moteur est hors ligne…" : "Écris à Jarvis…"}
        value={text}
        disabled={disabled}
        aria-describedby={hintId}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={onKeyDown}
      />
      <p id={hintId} className="visually-hidden">
        Entrée pour envoyer, Maj et Entrée pour aller à la ligne.
      </p>
      {voice &&
        (voice.active ? (
          <button
            type="button"
            className={styles.voice}
            data-active="true"
            onClick={voice.onStop}
            aria-label="Arrêter la voix"
          >
            <Square aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            className={styles.voice}
            disabled={disabled}
            onClick={voice.onListen}
            aria-label="Parler à Jarvis"
            title="Parler sans dire « Hey Jarvis »"
          >
            <Mic aria-hidden="true" />
          </button>
        ))}
      {busy ? (
        <button
          type="button"
          className={styles.send}
          onClick={onStop}
          aria-label="Arrêter la réponse"
        >
          <Square aria-hidden="true" />
        </button>
      ) : (
        <button
          type="submit"
          className={styles.send}
          disabled={disabled || !text.trim()}
          aria-label="Envoyer"
        >
          <ArrowUp aria-hidden="true" />
        </button>
      )}
    </form>
  );
}
