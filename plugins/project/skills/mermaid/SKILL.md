---
name: mermaid
description: House style for Mermaid diagrams in projects built with this marketplace. Load it before writing, editing or reviewing any Mermaid diagram (a ```mermaid block) anywhere, in chat replies, plans, design specs, ADRs or docs pages. Covers when to draw at all, which diagram type and shapes to use, the stroke-only color palette and the required style lines per diagram type.
---

# Mermaid house style

Diagrams render in GitHub, the VS Code Markdown preview (extension `bierner.markdown-mermaid`)
and Sphinx, in light and dark mode. The rules below keep them consistent everywhere.
`<skill-dir>/scripts/check_mermaid.py` enforces the mechanical rules. Projects generated from
the template carry a copy in `scripts/agent/check_mermaid.py`, which runs after every agent edit
and as a git hook.

## 1. Draw only when a picture beats text

- Three items or a straight chain: write a list or a table instead.
- One diagram answers one question. Split diagrams above about 15 nodes.
- Show only the members, edges and labels that matter for the question.

## 2. Pick the established type that fits

| Question the diagram answers | Type |
| --- | --- |
| Which parts exist and what depends on what (layers, components, data flow, pipeline) | `flowchart` |
| Which types exist, their fields, protocols and relations | `classDiagram` |
| Who calls whom, in which order (requests, protocols) | `sequenceDiagram` |
| Which states one thing moves through | `stateDiagram-v2` |
| Which tables or records exist and how they relate by key | `erDiagram` |
| What happens when, with real dates | `gantt` |

- Use `flowchart`, never `graph`. Use `stateDiagram-v2`, never `stateDiagram`.
- No beta or experimental types (`architecture-beta`, `block-beta`, `C4Context`, ...). GitHub
  and VS Code lag behind Mermaid releases.
- No new `A@{ shape: ... }` syntax. Use the classic shape brackets below.
- Direction: `TB` for hierarchies and layers, `LR` for pipelines and flows over time.

## 3. Shapes follow convention

Flowchart (ISO 5807 meanings):

| Shape | Syntax | Meaning |
| --- | --- | --- |
| Rectangle | `A[Text]` | Component, module, process step. The default. |
| Stadium | `A([Text])` | Start or end of a flow, or an external actor |
| Diamond | `A{Text?}` | Decision. Label outgoing edges (`-->|yes|`). |
| Parallelogram | `A[/Text/]` | Data in or out: file, upload, request, response |
| Cylinder | `A[(Text)]` | Stored data: database, file store, model weights |
| Subroutine | `A[[Text]]` | External system or service called as a whole |

Edges in flowcharts:

- `-->` flow or dependency. In layer diagrams the arrow points from the importer to the imported.
- `-.->` optional, asynchronous, or "implements".
- No thick `==>` edges. Keep edge labels to one to three words.

Class diagrams use UML relations:

| Relation | Syntax |
| --- | --- |
| Inheritance | `Base <|-- Child` |
| Implements a Protocol | `Impl ..|> Protocol` |
| Association (holds a reference) | `A --> B` |
| Composition (owns, same lifetime) | `A *-- B` |
| Dependency (uses in a signature) | `A ..> B` |

- Stereotypes: `<<Protocol>>`, `<<enum>>`, `<<exception>>`, `<<abstract>>`.
- Constructors as `+ClassName(args)`, never `__init__` (Mermaid renders the underscores as bold).
- Short member lines. Long signatures wrap and overlap. Name parameters without types when the
  line gets long, and explain the types in prose.

## 4. Color: strokes only, neutral by default

- No fills. Every node, subgraph, note and participant has `fill:none`.
- Text takes the stroke color of its shape: `color:#868e96` for neutral shapes, the group color
  for group shapes. Never rely on the theme's text color. Renderers can pick a theme that does not
  match the page (the VS Code preview drew dark text on a dark editor), and without fills that
  text disappears.
- Exception: edge labels keep the theme's label box and text. The box carries its own contrast,
  and a transparent box lets the edge line strike through the text.
- Everything is neutral gray `#868e96` unless it belongs to a highlighted group.
- Color a group only when the grouping carries meaning the layout does not show. Use at most
  four groups per diagram and name them in one line of prose below the diagram.
- Same concept, same color within a diagram.
- Only these strokes are allowed. Each has at least 3:1 contrast on white, GitHub dark
  (`#0d1117`), GitHub dimmed (`#22272e`) and VS Code dark (`#1e1e1e`):

| Name | Hex | Use |
| --- | --- | --- |
| neutral | `#868e96` | Default for nodes, subgraphs and edges |
| blue | `#2f81f7` | Group 1 |
| orange | `#e8710a` | Group 2 |
| green | `#1a9e5b` | Group 3 |
| purple | `#a371f7` | Group 4 |
| magenta | `#d6409f` | Extra group, only if needed |
| red | `#e5534b` | Errors, rejections, failure paths only |

- Group strokes are `stroke-width:2px`. Neutral strokes stay `1px`.
- No `%%{init}` directives and no `theme:` setting. They break the light and dark switch.

## 5. Required style lines per type

### Standard frontmatter, every type except gantt

Native style statements cannot reach edge lines, markers, ER rows or sequence text. This block
does. Copy it unchanged as the first lines of every diagram; the checker compares it verbatim.

```text
---
config:
  themeCSS: >-
    .row-rect-odd path, .row-rect-even path, marker circle { fill: none !important; }
    .edgePaths path, .flowchart-link, .relation, .transition, .relationshipLine, marker path, marker circle { stroke: #868e96 !important; }
    circle.state-start, circle.state-end { fill: #868e96 !important; stroke: #868e96 !important; }
    rect.actor, rect.note, rect[class^=activation], polygon.labelBox { fill: none !important; stroke: #868e96 !important; }
    line[class^=actor-line], line.loopLine, line[class^=messageLine], path[class^=messageLine] { stroke: #868e96 !important; }
    text.actor, text.actor tspan, .messageText, .noteText, .noteText tspan, .labelText, .labelText tspan, .loopText, .loopText tspan, text.text { fill: #868e96 !important; }
---
```

Then add the native lines for the type. Every `classDef` and `style` line has `fill:none`, a
`stroke` from the palette and a `color` equal to that stroke.

### flowchart

```text
flowchart TB
  classDef default fill:none,stroke:#868e96,stroke-width:1px,color:#868e96
  classDef blue fill:none,stroke:#2f81f7,stroke-width:2px,color:#2f81f7
  linkStyle default stroke:#868e96

  subgraph core[Core]
    A[Component]:::blue
  end
  style core fill:none,stroke:#868e96,stroke-dasharray:4 3,color:#868e96
```

- Every subgraph needs its own `style <id> fill:none,...` line. Dashed neutral by default; use a
  group color instead of dashes when the subgraph itself is the group.
- `linkStyle` sets the stroke only, never `color`: edge labels keep the theme's box and text.
- Define only the group classes you use.

### classDiagram

`classDef` does not remove fills in class diagrams. Give every class a `style` line:

```text
classDiagram
  class Forecaster {
    <<Protocol>>
    +forecast(frame, spec) QuantileForecast
  }
  class NaiveForecaster
  NaiveForecaster ..|> Forecaster
  style Forecaster fill:none,stroke:#a371f7,stroke-width:2px,color:#a371f7
  style NaiveForecaster fill:none,stroke:#868e96,color:#868e96
```

- No `note` statements. Notes keep a yellow fill. Put the text in prose below the diagram.

### sequenceDiagram

Participants cannot be styled one by one; the standard frontmatter makes them neutral.

```text
sequenceDiagram
  box transparent Service
    participant A as API
  end
```

- `box` groups only as `box transparent <Label>`. No `rect rgb(...)` highlights.
- `alt`, `opt` and `loop` frames are fine.

### stateDiagram-v2

`default` is reserved here. Define `base` and assign every state:

```text
stateDiagram-v2
  classDef base fill:none,stroke:#868e96,color:#868e96
  classDef red fill:none,stroke:#e5534b,stroke-width:2px,color:#e5534b
  [*] --> Uploaded
  Uploaded --> Ambiguous
  class Uploaded base
  class Ambiguous red
```

### erDiagram

```text
erDiagram
  classDef default fill:none,stroke:#868e96,color:#868e96
  SERIES ||--o{ OBSERVATION : has
```

### gantt

No frontmatter and no style lines. Bars keep theme colors.

## 6. Before you hand a diagram over

1. Render it if `mmdc` is available, in matching and mismatched themes, and check every text
   for legibility and overlap:
   - `mmdc -t default -b white -i d.mmd -o light.png`
   - `mmdc -t dark -b '#0d1117' -i d.mmd -o dark.png`
   - `mmdc -t default -b '#1e1e1e' -i d.mmd -o light-on-dark.png`
   - `mmdc -t dark -b white -i d.mmd -o dark-on-light.png`
2. Run `uv run --script scripts/agent/check_mermaid.py <file.md>` (or
   `<skill-dir>/scripts/check_mermaid.py` in projects without the copy) and fix every finding.
3. In chat replies, the terminal shows the source only. Follow the same rules anyway, so the
   diagram can be pasted into a doc unchanged.
