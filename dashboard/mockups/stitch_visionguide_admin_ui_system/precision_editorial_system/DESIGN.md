---
name: Precision Editorial System
colors:
  surface: '#faf8ff'
  surface-dim: '#d8d9e6'
  surface-bright: '#faf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f3ff'
  surface-container: '#ecedfa'
  surface-container-high: '#e6e7f4'
  surface-container-highest: '#e0e2ee'
  on-surface: '#181b24'
  on-surface-variant: '#444655'
  inverse-surface: '#2d303a'
  inverse-on-surface: '#eff0fd'
  outline: '#757687'
  outline-variant: '#c5c5d8'
  surface-tint: '#2e4ce1'
  primary: '#002ec6'
  on-primary: '#ffffff'
  primary-container: '#2c4be0'
  on-primary-container: '#cfd4ff'
  inverse-primary: '#bac3ff'
  secondary: '#006c46'
  on-secondary: '#ffffff'
  secondary-container: '#8ff4be'
  on-secondary-container: '#007149'
  tertiary: '#673b00'
  on-tertiary: '#ffffff'
  tertiary-container: '#885000'
  on-tertiary-container: '#ffce9e'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dee0ff'
  primary-fixed-dim: '#bac3ff'
  on-primary-fixed: '#00105b'
  on-primary-fixed-variant: '#002fc9'
  secondary-fixed: '#92f7c1'
  secondary-fixed-dim: '#76daa6'
  on-secondary-fixed: '#002112'
  on-secondary-fixed-variant: '#005234'
  tertiary-fixed: '#ffdcbd'
  tertiary-fixed-dim: '#ffb86e'
  on-tertiary-fixed: '#2c1600'
  on-tertiary-fixed-variant: '#693c00'
  background: '#faf8ff'
  on-background: '#181b24'
  surface-variant: '#e0e2ee'
typography:
  display:
    fontFamily: Inter
    fontSize: 2rem
    fontWeight: '600'
    lineHeight: 2.25rem
    letterSpacing: -0.025em
  headline-lg:
    fontFamily: Inter
    fontSize: 1.5rem
    fontWeight: '600'
    lineHeight: 1.875rem
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 1.25rem
    fontWeight: '600'
    lineHeight: 1.625rem
    letterSpacing: -0.018em
  headline-sm:
    fontFamily: Inter
    fontSize: 1.125rem
    fontWeight: '500'
    lineHeight: 1.5rem
    letterSpacing: -0.015em
  body-lg:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: '400'
    lineHeight: 1.5rem
    letterSpacing: -0.011em
  body-md:
    fontFamily: Inter
    fontSize: 0.875rem
    fontWeight: '400'
    lineHeight: 1.25rem
    letterSpacing: -0.006em
  body-sm:
    fontFamily: Inter
    fontSize: 0.8125rem
    fontWeight: '400'
    lineHeight: 1.125rem
    letterSpacing: 0em
  label-md:
    fontFamily: Inter
    fontSize: 0.875rem
    fontWeight: '500'
    lineHeight: 1.25rem
    letterSpacing: -0.006em
  label-sm:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: '500'
    lineHeight: 1rem
    letterSpacing: 0.01em
  caption:
    fontFamily: Inter
    fontSize: 0.6875rem
    fontWeight: '500'
    lineHeight: 0.875rem
    letterSpacing: 0.02em
  code:
    fontFamily: JetBrains Mono
    fontSize: 0.8125rem
    fontWeight: '400'
    lineHeight: 1.25rem
    letterSpacing: 0em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1rem
  gutter-desktop: 1.5rem
  margin: 1rem
  margin-desktop: 2rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system delivers a high-density, utility-first administrative interface optimized for operational efficiency, rapid data comprehension, and rigorous architectural clarity. Taking cues from modern developer and product-management platforms, the aesthetic balances extreme structural restraint with razor-sharp detailing.

The personality is purposeful, analytical, and authoritative. It removes all non-essential visual ornamentation in favor of meticulous alignment, typographic precision, and intentional color accents that communicate system state instantly. Users should experience zero friction, high predictability, and the confidence of piloting professional-grade instrumentation.

## Colors

The palette is engineered for prolonged operational focus, utilizing an off-white warm baseline paired with clinical neutrals and highly targeted semantic accents.

- **Primary (`#2c4be0`)**: Reserved strictly for high-priority interactive affordances, active navigation items, key toggles, and direct CTAs.
- **Success (`#15875a`)**: Used for healthy statuses, active deployment markers, and positive trend vectors.
- **Warning (`#d98a2b`)**: Denotes pending operations, throttling, review thresholds, and non-blocking warnings.
- **Danger (`#d3372c`)**: Signals destructive operations, offline statuses, network faults, and critical incidents.
- **Background (`#fafaf8`)**: The primary application canvas providing low eye strain across extended monitoring periods.
- **Surface (`#ffffff`)**: Used for cards, panels, popovers, tables, and overlay sheets.
- **Border (`#e7e5df`)**: A delicate, structural dividing line applied uniformly across all components and dividers.
- **Body (`#1b1e27`)**: High-contrast, near-black tone reserved for primary headings, table text, and active values.
- **Muted Text (`#6b6f7a`)**: Secondary metadata, tabular headers, labels, and disabled states.

## Typography

Typography relies on dense vertical metrics and subtle negative tracking to preserve clarity in data-dense layouts. Numerical figures, operational stats, and codes leverage tabular formatting to prevent layout shifts during live data polling.

Hierarchy is enforced through weight differentials (`500` and `600`) and value steps rather than dramatic shifts in scale. Captions and status indicators adopt slight positive tracking for micro-legibility at 11px and 12px.

## Layout & Spacing

The layout is built around a flexible 12-column grid system bounded by fixed desktop maximum constraints (`1440px`) or full-viewport dashboards depending on view mode. Spacing is anchored to a 4px baseline rhythm.

- **Desktop (>= 1024px)**: Fixed left rail (`240px`), variable secondary pane (`280px–360px`), and fluid content core utilizing `1.5rem` gutters and `2rem` outer padding.
- **Tablet (768px - 1023px)**: Left rail collapses to a micro-rail (`64px`), content switches to `1rem` gutters.
- **Mobile (< 768px)**: Navigation docks to an off-canvas drawer; grids collapse into a single stacked column with `1rem` edge margins.

## Elevation & Depth

Depth is established primarily through crisp border delineations (`1px solid #e7e5df`) supplemented by delicate, low-contrast shadows. Surface layers elevate only when separating context or indicating interactivity.

- **Flat / Surface Level**: `0 0 0 1px #e7e5df` (Cards, panels, table headers).
- **Interactive Rest**: `0 1px 2px rgba(0, 0, 0, 0.04), 0 0 0 1px #e7e5df` (Buttons, inputs).
- **Floating Panels / Dropdowns**: `0 4px 12px rgba(0, 0, 0, 0.05), 0 0 0 1px #e7e5df`.
- **Modals & Overlays**: `0 12px 32px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(0, 0, 0, 0.08)`.

## Shapes

The geometric framework uses intentional corner radii to communicate interactive scale:

- **Inputs, Buttons, Badges, Tabs (`6px`)**: Compact form factor preventing visual softness while preserving modern ergonomics.
- **Cards, Modals, Flyouts, Containers (`10px`)**: Structural boundaries holding nested components.
- **Status Dots & Avatars (`9999px`)**: Fully circular treatments restricted strictly to presence indicators, user profiles, and micro metric pills.

## Components

### Buttons
- **Primary**: Background `#2c4be0`, text `#ffffff`, height `32px` (standard) or `28px` (dense), radius `6px`, font weight `500`. Hover: `#223ec2`. Active: scale `0.99`.
- **Secondary**: Surface `#ffffff`, border `1px solid #e7e5df`, text `#1b1e27`. Hover: `#fafaf8`, border `#d8d6ce`.
- **Ghost**: Background transparent, text `#6b6f7a`. Hover: background `#fafaf8`, text `#1b1e27`.
- **Danger**: Surface `#ffffff`, border `1px solid #e7e5df`, text `#d3372c`. Hover: background `#fef2f2`, border `#fca5a5`. Filled danger variant reserved for confirmation modals: background `#d3372c`, text `#ffffff`.

### Status Badges & Indicators
- Built on a base height of `20px`, padding `0 6px`, radius `4px` or full pill, font size `11px`, weight `500`.
- **Online**: Background `rgba(21, 135, 90, 0.1)`, text `#15875a`, dot indicator `#15875a`.
- **Warning**: Background `rgba(217, 138, 43, 0.1)`, text `#d98a2b`, dot indicator `#d98a2b`.
- **Offline / Critical**: Background `rgba(211, 55, 44, 0.1)`, text `#d3372c`, dot indicator `#d3372c`.
- **Unknown / Muted**: Background `rgba(107, 111, 122, 0.1)`, text `#6b6f7a`, dot indicator `#6b6f7a`.

### Input Fields
- Height `32px`, border radius `6px`, background `#ffffff`, border `1px solid #e7e5df`, text `#1b1e27`, placeholder `#6b6f7a`.
- **Focus**: Border `#2c4be0`, outline `2px solid rgba(44, 75, 224, 0.15)`, outline-offset `0px`.
- **Error**: Border `#d3372c`, outline `2px solid rgba(211, 55, 44, 0.15)`.

### Cards & Data Panels
- Background `#ffffff`, border `1px solid #e7e5df`, border radius `10px`, inner padding `16px` to `20px`.
- Header sections within cards include bottom divider line `1px solid #e7e5df` and tight header-to-action spacing with zero margin leakage.