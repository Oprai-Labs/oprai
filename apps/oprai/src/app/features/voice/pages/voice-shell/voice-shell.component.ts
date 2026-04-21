import {
  Component,
  signal,
  computed,
  OnDestroy,
  NgZone,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { LucideAngularModule } from 'lucide-angular';
import { MessageListComponent } from '../../../chat/components/message-list/message-list.component';
import { ChatMessage } from '../../../chat/services/chat-api.service';

export type VoiceState = 'idle' | 'connecting' | 'listening' | 'thinking' | 'speaking';

let nextMsgId = 1;

function makeMsg(role: 'user' | 'assistant', content: string, sessionId = 'voice-demo'): ChatMessage {
  return {
    id: `voice-${nextMsgId++}`,
    sessionId,
    role,
    content,
    createdAt: new Date().toISOString(),
  };
}

const DEMO_EXCHANGES: { user: string; assistant: string }[] = [
  { user: 'What\'s the current price of SOL?', assistant: 'SOL is currently trading at $148.32, up 3.2% in the last 24 hours. The market cap is $68.4B with a 24h volume of $2.1B.' },
  { user: 'Send 0.5 SOL to phantom.sol', assistant: 'I\'ll prepare a transfer of 0.5 SOL to phantom.sol. Please confirm the transaction in your wallet to proceed.' },
  { user: 'Show me trending tokens today', assistant: 'Here are today\'s trending tokens on Solana:\n1. BONK — up 12.4%\n2. JTO — up 8.7%\n3. WIF — up 6.1%\n4. PYTH — up 4.3%' },
];

@Component({
  selector: 'app-voice-shell',
  standalone: true,
  imports: [CommonModule, RouterLink, LucideAngularModule, MessageListComponent],
  templateUrl: './voice-shell.component.html',
  styleUrl: './voice-shell.component.scss',
})
export class VoiceShellComponent implements OnDestroy {
  private readonly ngZone = inject(NgZone);

  readonly voiceState = signal<VoiceState>('idle');
  readonly isMuted = signal(false);
  readonly isSpeakerOn = signal(true);
  readonly elapsedTime = signal(0);
  readonly messages = signal<ChatMessage[]>([]);
  readonly streaming = signal(false);

  readonly isActive = computed(() => {
    const s = this.voiceState();
    return s !== 'idle' && s !== 'connecting';
  });

  readonly statusText = computed(() => {
    switch (this.voiceState()) {
      case 'idle':
        return 'Tap the mic to start';
      case 'connecting':
        return 'Connecting...';
      case 'listening':
        return 'Listening...';
      case 'thinking':
        return 'Processing...';
      case 'speaking':
        return 'Speaking...';
    }
  });

  readonly formattedTime = computed(() => {
    const t = this.elapsedTime();
    const m = Math.floor(t / 60);
    const s = t % 60;
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  });

  private timerInterval: ReturnType<typeof setInterval> | null = null;
  private audioContext: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private demoTimeout: ReturnType<typeof setTimeout> | null = null;
  private streamInterval: ReturnType<typeof setInterval> | null = null;
  private demoIndex = 0;

  ngOnDestroy(): void {
    this.cleanup();
  }

  async toggleVoice(): Promise<void> {
    if (this.voiceState() === 'idle') {
      await this.startVoice();
    } else {
      this.endVoice();
    }
  }

  toggleMute(): void {
    this.isMuted.update((v) => !v);
    if (this.mediaStream) {
      this.mediaStream.getAudioTracks().forEach((t) => {
        t.enabled = !this.isMuted();
      });
    }
  }

  toggleSpeaker(): void {
    this.isSpeakerOn.update((v) => !v);
  }

  endVoice(): void {
    this.voiceState.set('idle');
    this.stopTimer();
    this.stopAudio();
    this.elapsedTime.set(0);
    this.messages.set([]);
    this.streaming.set(false);
    this.clearDemoTimers();
  }

  private async startVoice(): Promise<void> {
    this.voiceState.set('connecting');
    this.demoIndex = 0;

    try {
      this.audioContext = new AudioContext();
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      // Continue in demo mode
    }

    await new Promise((r) => setTimeout(r, 1000));
    this.voiceState.set('listening');
    this.startTimer();
    this.startDemoCycle();
  }

  private startTimer(): void {
    this.timerInterval = setInterval(() => {
      this.ngZone.run(() => this.elapsedTime.update((t) => t + 1));
    }, 1000);
  }

  private stopTimer(): void {
    if (this.timerInterval) { clearInterval(this.timerInterval); this.timerInterval = null; }
  }

  private stopAudio(): void {
    if (this.mediaStream) { this.mediaStream.getTracks().forEach((t) => t.stop()); this.mediaStream = null; }
    if (this.audioContext) { this.audioContext.close(); this.audioContext = null; }
  }

  private clearDemoTimers(): void {
    if (this.demoTimeout) { clearTimeout(this.demoTimeout); this.demoTimeout = null; }
    if (this.streamInterval) { clearInterval(this.streamInterval); this.streamInterval = null; }
  }

  private startDemoCycle(): void {
    const cycle = () => {
      if (this.voiceState() === 'idle' || this.voiceState() === 'connecting') return;

      const exchange = DEMO_EXCHANGES[this.demoIndex % DEMO_EXCHANGES.length];

      // Phase 1: listening (user speaks)
      this.voiceState.set('listening');
      this.demoTimeout = setTimeout(() => {
        if (this.voiceState() === 'idle') return;

        // Add user message
        this.messages.update((msgs) => [...msgs, makeMsg('user', exchange.user)]);

        // Phase 2: thinking
        this.voiceState.set('thinking');
        // Add empty assistant message for thinking indicator
        const assistantMsg = makeMsg('assistant', '');
        this.messages.update((msgs) => [...msgs, assistantMsg]);
        this.streaming.set(true);

        this.demoTimeout = setTimeout(() => {
          if (this.voiceState() === 'idle') return;

          // Phase 3: speaking — stream the response character by character
          this.voiceState.set('speaking');
          const fullText = exchange.assistant;
          let charIndex = 0;

          this.streamInterval = setInterval(() => {
            if (this.voiceState() === 'idle') {
              if (this.streamInterval) { clearInterval(this.streamInterval); this.streamInterval = null; }
              return;
            }

            // Stream ~3 chars at a time for realistic speed
            const chunkEnd = Math.min(charIndex + 3, fullText.length);
            const partial = fullText.slice(0, chunkEnd);
            charIndex = chunkEnd;

            this.ngZone.run(() => {
              this.messages.update((msgs) => {
                const updated = [...msgs];
                const lastIdx = updated.length - 1;
                updated[lastIdx] = { ...updated[lastIdx], content: partial };
                return updated;
              });
            });

            if (charIndex >= fullText.length) {
              if (this.streamInterval) { clearInterval(this.streamInterval); this.streamInterval = null; }
              this.streaming.set(false);

              // Pause, then start next exchange
              this.demoTimeout = setTimeout(() => {
                this.demoIndex++;
                cycle();
              }, 2000);
            }
          }, 30);
        }, 1500);
      }, 3000);
    };

    cycle();
  }

  private cleanup(): void {
    this.stopTimer();
    this.stopAudio();
    this.clearDemoTimers();
  }
}
