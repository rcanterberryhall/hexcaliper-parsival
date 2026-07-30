# Why a graph

A walk-through, in four pictures, of why the work products in industrial safety engineering naturally form a graph — and what that unlocks.

---

## 1. What you have today

```mermaid
graph TB
    subgraph "Drawing package"
        D1["Wiring diagrams"]
        D2["P&IDs"]
        D3["Loop sheets"]
        D4["I/O list"]
    end
    subgraph "Logic"
        L1["PLC code"]
        L2["HMI screens"]
    end
    subgraph "Safety case"
        S1["Hazard analysis"]
        S2["SRS"]
        S3["FMEA"]
        S4["SIL calculations"]
    end
    subgraph "Verification"
        V1["FAT procedures"]
        V2["SAT procedures"]
        V3["Calibration records"]
    end

    classDef doc fill:#1a1530,stroke:#6b5f8a,color:#ede8f5
    class D1,D2,D3,D4,L1,L2,S1,S2,S3,S4,V1,V2,V3 doc
```

Thirteen documents. Each one internally complete, formally reviewed, signed off. No connections drawn between them — because there's no document in the project where those connections live.

This is the reality. This is what gets handed over at the end of a project.

---

## 2. What you actually do with them

```mermaid
graph TB
    ENG(("Senior<br/>engineer"))

    D1["Wiring diagrams"]
    D2["P&IDs"]
    L1["PLC code"]
    S1["Hazard analysis"]
    S2["SRS"]
    S3["FMEA"]
    V2["SAT procedures"]

    ENG -.->|"'where is this<br/>tag wired?'"| D1
    ENG -.->|"'what does this<br/>logic do?'"| L1
    ENG -.->|"'why is this<br/>setpoint 85%?'"| S1
    ENG -.->|"'what test<br/>covers this?'"| V2
    ENG -.->|"'what failure mode<br/>is this guarding?'"| S3
    ENG -.->|"'what does the<br/>spec require?'"| S2
    ENG -.->|"'where does this<br/>signal go?'"| D2

    classDef doc fill:#1a1530,stroke:#6b5f8a,color:#ede8f5
    classDef person fill:#3d2d18,stroke:#d4a017,color:#fbbf24
    class D1,D2,L1,S1,S2,S3,V2 doc
    class ENG person
```

When something needs to happen — an MOC scoped, an incident investigated, a junior onboarded, a bid scoped — the engineer becomes a human index. They open document A, find a tag, switch to document B, find what reads it, switch to document C, find what tests it.

The connections are real. They're just not written down. They live between the engineer's ears, and they leave the company when the engineer does.

---

## 3. The connections, drawn

```mermaid
graph TB
    D1["Wiring diagrams"]
    D2["P&IDs"]
    L1["PLC code"]
    S1["Hazard analysis"]
    S2["SRS"]
    S3["FMEA"]
    V2["SAT procedures"]

    D1 ---|shared tags| D2
    D1 ---|shared tags| L1
    D2 ---|shared equipment| S1
    S1 ---|requires| S2
    S2 ---|allocates to| L1
    S2 ---|verified by| V2
    S3 ---|failure modes of| D1
    S3 ---|detected by| L1
    V2 ---|tests| L1
    V2 ---|tests| D1
    S1 ---|mitigated by| L1

    classDef doc fill:#1a1530,stroke:#6b5f8a,color:#ede8f5
    class D1,D2,L1,S1,S2,S3,V2 doc

    linkStyle 0,1,2 stroke:#9b5de5,stroke-width:2px
    linkStyle 3,4,5,6,7,8,9,10 stroke:#d4a017,stroke-width:2px
```

The same documents. The same connections the engineer was tracing in their head. Now visible.

This is a graph. Not because anyone designed it as one — because that's what the structure of the work *is*. The documents are nodes. The relationships between them are edges. We didn't impose a graph; we made the existing one visible.

The purple edges (shared tags, shared equipment) are mechanically derivable — same string appears in both documents. The gold edges (requires, allocates to, verified by, mitigates) are semantic — they describe meaning, not just co-occurrence. Both kinds are real. Both are traversed daily. Neither is written down.

---

## 4. What changes when the graph exists outside the engineer's head

```mermaid
graph LR
    GRAPH[("Connected<br/>graph of<br/>artifacts")]

    Q1["'What does this<br/>SAT step verify?'"]
    Q2["'What's the blast<br/>radius of this<br/>logic change?'"]
    Q3["'Where are the<br/>coverage gaps?'"]
    Q4["'Generate the<br/>traceability matrix'"]
    Q5["'Regenerate the SAT<br/>after the MOC'"]
    Q6["'Show me every<br/>document touching<br/>this device'"]

    GRAPH -->|query| Q1
    GRAPH -->|query| Q2
    GRAPH -->|query| Q3
    GRAPH -->|derive| Q4
    GRAPH -->|derive| Q5
    GRAPH -->|traverse| Q6

    classDef graphnode fill:#3d2d18,stroke:#d4a017,color:#fbbf24
    classDef query fill:#1e3d2a,stroke:#4ade80,color:#86efac
    class GRAPH graphnode
    class Q1,Q2,Q3,Q4,Q5,Q6 query
```

Each of those questions is, today, a senior engineer with a stack of documents and several hours. Each one becomes a query when the graph is explicit.

None of these are AI tasks in the LLM sense. They're traversals. The graph does the work. The LLM, when it shows up at all, is at the edges — extracting structure from documents during ingestion, generating prose during rendering, helping a user phrase a query they don't quite know how to ask.

The valuable thing isn't a smarter document search. It's making the structure that already exists — in the engineer's head, in the work itself — explicit, queryable, auditable, and durable beyond any one engineer's tenure.

---

## The argument, compressed

The graph isn't a clever model imposed on engineering work. It's the honest representation of work that's already graph-shaped, currently encoded as a pile of documents and a senior engineer's memory. Making it explicit doesn't change the work — it changes who can do the work, how fast, and how reliably.
