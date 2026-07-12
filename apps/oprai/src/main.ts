import { Buffer } from 'buffer';
(window as any)['Buffer'] = Buffer;

import { bootstrapApplication } from '@angular/platform-browser';
import {
  provideRouter,
  withComponentInputBinding,
  withViewTransitions,
} from '@angular/router';
import {
  provideHttpClient,
  withInterceptors,
  withFetch,
} from '@angular/common/http';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { importProvidersFrom } from '@angular/core';
import {
  LucideAngularModule,
  LayoutDashboard, SquarePen, MessageSquare, Bot, Briefcase,
  TrendingUp, Eye, Search, Zap, BarChart3, ShoppingBag, Settings,
  ChevronDown, ChevronLeft, ChevronRight, MoreVertical, Pencil,
  Trash2, Menu, ArrowRight, Paperclip, SendHorizontal, CheckCircle2,
  AlertCircle, Sun, Moon, LogOut, X, CreditCard, Activity, PieChart,
  CircleDollarSign, Clock, Wallet, Copy, RefreshCw, Flame,
  TrendingDown, Sparkles, Crown, Trophy, PackagePlus, Plus,
  Layers, Shield, ArrowRightLeft, ArrowDownUp, Bell, Info, KeyRound, Users,
  ExternalLink, Timer, Crosshair, Sprout, Droplets, Landmark,
  Vault, Rocket, Tag, Magnet, ScanSearch, Scale, ClipboardList,
  ShieldAlert, PenTool, Image, Grid2x2, LayoutGrid, Fuel, Coins,
  Repeat, ArrowUpRight, ArrowDownLeft, FileText, GitBranch, Lock, User,
  List, Brain, ShieldCheck, Palette, ReceiptText, CheckCircle,
  TriangleAlert, DollarSign, RotateCcw, HelpCircle, Banknote,
  Percent, Star, Code, Send, Compass, Cable, Waves, Vote,
  BellRing, Webhook, Unlock, Gift, Target, HandCoins, XCircle,
  PanelLeftClose, PanelLeftOpen, Ellipsis, ArrowUp, ArrowDown,
  FolderPlus, FolderOpen, Folder, EyeOff, Ghost, AudioLines, History,
  Globe, CircleHelp, Share2, MessageCircleQuestion, Flag,
  HardDrive, Check, Lightbulb, File,
  Monitor, Download, Database, MessageCircle, Twitter,
  Smartphone, ChevronUp, CandlestickChart, AreaChart,
  Mic, MicOff, Phone, PhoneOff, Volume2, VolumeX,
  Minimize2, Maximize2, Pin, PinOff,
} from 'lucide-angular';

import { AppComponent } from './app/app.component';
import { routes } from './app/app.routes';
import { authInterceptor } from './app/core/interceptors/auth.interceptor';
import { errorInterceptor } from './app/core/interceptors/error.interceptor';
import { Tesseract } from './app/core/icons/tesseract.icon';
import { Portfolio } from './app/core/icons/portfolio.icon';

bootstrapApplication(AppComponent, {
  providers: [
    provideRouter(routes, withComponentInputBinding(), withViewTransitions()),
    provideHttpClient(
      withFetch(),
      withInterceptors([authInterceptor, errorInterceptor])
    ),
    provideAnimationsAsync(),
    importProvidersFrom(LucideAngularModule.pick({
      LayoutDashboard, SquarePen, MessageSquare, Bot, Briefcase,
      TrendingUp, Eye, Search, Zap, BarChart3, ShoppingBag, Settings,
      ChevronDown, ChevronLeft, ChevronRight, MoreVertical, Pencil,
      Trash2, Menu, ArrowRight, Paperclip, SendHorizontal, CheckCircle2,
      AlertCircle, Sun, Moon, LogOut, X, CreditCard, Activity, PieChart,
      CircleDollarSign, Clock, Wallet, Copy, RefreshCw, Flame,
      TrendingDown, Sparkles, Crown, Trophy, PackagePlus, Plus,
      Layers, Shield, ArrowRightLeft, ArrowDownUp, Bell, Info, KeyRound, Users,
      ExternalLink, Timer, Crosshair, Sprout, Droplets, Landmark,
      Vault, Rocket, Tag, Magnet, ScanSearch, Scale, ClipboardList,
      ShieldAlert, PenTool, Image, Grid2x2, LayoutGrid, Fuel, Coins,
      Repeat, ArrowUpRight, ArrowDownLeft, FileText, GitBranch, Lock, User,
      List, Brain, ShieldCheck, Palette, ReceiptText, CheckCircle,
      TriangleAlert, DollarSign, RotateCcw, HelpCircle, Banknote,
      Percent, Star, Code, Send, Compass, Cable, Waves, Vote,
      BellRing, Webhook, Unlock, Gift, Target, HandCoins, XCircle,
      PanelLeftClose, PanelLeftOpen, Ellipsis, ArrowUp, ArrowDown,
      FolderPlus, FolderOpen, Folder, EyeOff, Ghost, AudioLines, History,
      Globe, CircleHelp, Share2, MessageCircleQuestion, Flag,
      HardDrive, Check, Lightbulb, File,
      Monitor, Download, Database, MessageCircle, Twitter,
      Smartphone, ChevronUp, CandlestickChart, AreaChart,
      Mic, MicOff, Phone, PhoneOff, Volume2, VolumeX,
      Minimize2, Maximize2, Pin, PinOff,
      Tesseract,
      Portfolio,
    })),
  ],
}).catch((err) => console.error(err));
