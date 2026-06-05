importScripts("https://unpkg.com/neo4j-driver");

let driver = null;

self.onmessage = function(e) {
    if (e.data.type === 'CONNECT') {
        if (driver) driver.close();
        driver = neo4j.driver(e.data.uri, neo4j.auth.basic(e.data.user, e.data.pass));
        driver.verifyConnectivity().then(() => {
            self.postMessage({ type: 'CONNECTED' });
        }).catch(err => {
            self.postMessage({ type: 'ERROR', error: err.message });
        });
    } else if (e.data.type === 'DISCONNECT') {
        if (driver) {
            driver.close();
            driver = null;
        }
    } else if (e.data.type === 'FETCH_GRAPH') {
        if (!driver) {
            self.postMessage({ type: 'ERROR', error: "Driver not initialized" });
            return;
        }
        const session = driver.session();
        let nodeMap = new Map();
        let nodeBuffer = [];
        let edgeBuffer = [];
        let pCount = 0;
        let cCount = 0;
        
        const isLight = e.data.isLight;

        session.run("MATCH (n) OPTIONAL MATCH (n)-[r]->(m) RETURN n, r, m")
            .subscribe({
                onNext: record => {
                    const n = record.get('n');
                    if (n && !nodeMap.has(n.identity.toString())) {
                        let pNode = parseNodeWorker(n, isLight);
                        nodeMap.set(n.identity.toString(), pNode);
                        nodeBuffer.push(pNode);
                        if (n.labels.includes("Paper")) pCount++;
                        if (n.labels.includes("Concept")) cCount++;
                    }
                    const m = record.get('m');
                    if (m && !nodeMap.has(m.identity.toString())) {
                        let pNode = parseNodeWorker(m, isLight);
                        nodeMap.set(m.identity.toString(), pNode);
                        nodeBuffer.push(pNode);
                        if (m.labels.includes("Paper")) pCount++;
                        if (m.labels.includes("Concept")) cCount++;
                    }
                    const r = record.get('r');
                    if (r) {
                        edgeBuffer.push({
                            id: r.identity.toString(),
                            from: r.start.toString(),
                            to: r.end.toString(),
                            label: r.type,
                            font: { strokeWidth: 0, color: isLight ? "rgba(0,0,0,0.4)" : "rgba(255,255,255,0.3)", size: 10, face: "Outfit" },
                            arrows: { to: { enabled: true, scaleFactor: 0.5 } },
                            color: { color: isLight ? "rgba(0,0,0,0.15)" : "rgba(255, 255, 255, 0.12)", highlight: "rgba(0, 229, 255, 0.4)" }
                        });
                    }

                    // Flush buffer to main thread gracefully
                    if (nodeBuffer.length >= 150 || edgeBuffer.length >= 500) {
                        self.postMessage({
                            type: 'BATCH',
                            nodes: nodeBuffer,
                            edges: edgeBuffer,
                            pCount: pCount,
                            cCount: cCount
                        });
                        nodeBuffer = [];
                        edgeBuffer = [];
                    }
                },
                onCompleted: () => {
                    if (nodeBuffer.length > 0 || edgeBuffer.length > 0) {
                        self.postMessage({
                            type: 'BATCH',
                            nodes: nodeBuffer,
                            edges: edgeBuffer,
                            pCount: pCount,
                            cCount: cCount
                        });
                    }
                    self.postMessage({ type: 'DONE', pCount: pCount, cCount: cCount });
                    session.close();
                },
                onError: error => {
                    self.postMessage({ type: 'ERROR', error: error.message });
                    session.close();
                }
            });
    }
};

function parseNodeWorker(node, isLight) {
    const id = node.identity.toString();
    const labels = node.labels;
    const label = labels[0] || "Unknown";
    const props = node.properties;
    
    let title = props.name || props.title || "Untitled";
    let color = "#fff";
    let shape = "dot";
    let size = 15;

    if (labels.includes("Paper")) {
        color = "#00e5ff";
        shape = "dot";
        size = 28;
    } else if (labels.includes("Concept")) {
        color = "#d500f9";
        shape = "dot";
        size = 18;
    } else if (labels.includes("Theorem")) {
        color = "#ffd600";
        shape = "diamond";
        size = 14;
    } else if (labels.includes("Algorithm")) {
        color = "#ff6d00";
        shape = "triangle";
        size = 14;
    } else if (labels.includes("CodeSnippet")) {
        color = "#00e676";
        shape = "square";
        size = 14;
    } else if (labels.includes("Diagram")) {
        color = "#ff1744";
        shape = "hexagon";
        size = 14;
    }

    return {
        id: id,
        label: title.length > 20 ? title.substring(0, 18) + "..." : title,
        title: title,
        shape: shape,
        size: size,
        color: {
            background: isLight ? "rgba(255, 255, 255, 0.9)" : "rgba(9, 13, 22, 0.9)",
            border: color,
            highlight: { background: isLight ? "rgba(0, 229, 255, 0.1)" : "rgba(0, 229, 255, 0.2)", border: "#00e5ff" }
        },
        borderWidth: 2,
        font: { color: isLight ? "#0f172a" : "#e2e8f0", size: 12, face: "Outfit", strokeWidth: 0 },
        properties: props,
        type: label
    };
}
