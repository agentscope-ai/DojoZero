import { useMemo } from "react";

/**
 * NBA Arena background with frontal perspective view:
 * - Trapezoidal perspective from front audience view
 * - Dynamic team-colored lighting
 * - Crowd silhouettes with depth
 * - Spotlight effects with perspective
 * - Arena structure elements in 3D space
 */
export default function ArenaBackground({ homeTeam, awayTeam }) {
  const homeColor = homeTeam?.color || "#3B82F6";
  const awayColor = awayTeam?.color || "#EF4444";
  
  // Generate crowd members with perspective depth
  const crowdMembers = useMemo(() => {
    const members = [];
    const rows = 5; // 5 depth levels
    
    for (let row = 0; row < rows; row++) {
      // Calculate perspective: farther rows have fewer, more condensed members
      const depth = row / (rows - 1); // 0 (far) to 1 (near)
      const rowWidth = 30 + depth * 70; // 30% at back, 100% at front
      const rowOffset = (100 - rowWidth) / 2; // Center the row
      const membersCount = Math.floor(25 + depth * 35); // 25 at back, 60 at front
      
      for (let i = 0; i < membersCount; i++) {
        const positionInRow = i / (membersCount - 1 || 1);
        const isHomeSection = positionInRow < 0.5;
        
        members.push({
          id: `${row}-${i}`,
          row,
          depth,
          position: rowOffset + positionInRow * rowWidth,
          height: 6 + depth * 10, // Smaller at back, larger at front
          width: 2 + depth * 2,
          delay: Math.random() * 3,
          team: isHomeSection ? 'home' : 'away',
          waveOffset: Math.random() * 2,
          opacity: 0.3 + depth * 0.3,
        });
      }
    }
    return members;
  }, []);

  return (
    <div style={styles.container}>
      {/* Perspective wrapper for 3D effect */}
      <div style={styles.perspectiveScene}>
        
        {/* Arena ceiling with converging lines */}
        <div style={styles.ceiling}>
          <svg style={styles.ceilingStructure} viewBox="0 0 100 100" preserveAspectRatio="none">
            {/* Converging vertical lines from top to vanishing point */}
            {[10, 20, 30, 40, 60, 70, 80, 90].map((x, i) => (
              <line 
                key={`v-${i}`}
                x1={x} y1="0" 
                x2="50" y2="35"
                stroke="rgba(255,255,255,0.04)"
                strokeWidth="0.3"
              />
            ))}
            {/* Horizontal beams with perspective */}
            {[0, 10, 20, 30].map((y, i) => {
              const width = 30 - y * 0.7; // Narrower as they go up
              const offset = (100 - (100 - width * 2)) / 2;
              return (
                <line 
                  key={`h-${i}`}
                  x1={offset} y1={y} 
                  x2={100 - offset} y2={y}
                  stroke={`rgba(255,255,255,${0.03 - i * 0.005})`}
                  strokeWidth="0.5"
                />
              );
            })}
          </svg>
          
          {/* Colored lighting from ceiling */}
          <div 
            style={{
              ...styles.ceilingLight,
              background: `
                radial-gradient(ellipse 25% 40% at 25% 20%, ${homeColor}25 0%, transparent 50%),
                radial-gradient(ellipse 25% 40% at 75% 20%, ${awayColor}25 0%, transparent 50%),
                radial-gradient(ellipse 40% 50% at 50% 30%, rgba(255,255,255,0.03) 0%, transparent 60%)
              `,
            }}
          />
        </div>

        {/* Trapezoidal spotlight beams with perspective */}
        <div style={styles.spotlightContainer}>
          {[
            { xTop: 15, xBottom: 5, color: homeColor, opacity: 0.08 },
            { xTop: 35, xBottom: 30, color: '#ffffff', opacity: 0.05 },
            { xTop: 65, xBottom: 70, color: '#ffffff', opacity: 0.05 },
            { xTop: 85, xBottom: 95, color: awayColor, opacity: 0.08 },
          ].map((spot, i) => (
            <svg
              key={i}
              style={styles.spotlightBeam}
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
            >
              <defs>
                <linearGradient id={`spotGrad-${i}`} x1="0%" y1="0%" x2="0%" y2="100%">
                  <stop offset="0%" stopColor={spot.color} stopOpacity={spot.opacity} />
                  <stop offset="100%" stopColor={spot.color} stopOpacity="0" />
                </linearGradient>
              </defs>
              <polygon
                points={`${spot.xTop},0 ${spot.xTop + 8},0 ${spot.xBottom + 12},100 ${spot.xBottom},100`}
                fill={`url(#spotGrad-${i})`}
              />
            </svg>
          ))}
        </div>

        {/* Crowd in perspective - arranged in trapezoid layers */}
        <div style={styles.crowdContainer}>
          {[0, 1, 2, 3, 4].map(rowIndex => {
            const rowMembers = crowdMembers.filter(m => m.row === rowIndex);
            const depth = rowIndex / 4;
            const topPosition = 5 + rowIndex * 12; // Spread out vertically
            
            return (
              <div 
                key={rowIndex}
                style={{
                  ...styles.crowdRow,
                  top: `${topPosition}%`,
                  height: '12%',
                }}
              >
                {/* Colored backdrop for crowd sections */}
                <div 
                  style={{
                    ...styles.crowdBackdrop,
                    background: `linear-gradient(90deg, 
                      ${homeColor}${Math.floor(15 + depth * 15).toString(16)} 0%, 
                      transparent 45%, 
                      transparent 55%, 
                      ${awayColor}${Math.floor(15 + depth * 15).toString(16)} 100%)`,
                  }}
                />
                
                {/* Individual crowd members */}
                {rowMembers.map(member => (
                  <div
                    key={member.id}
                    className="crowd-wave"
                    style={{
                      ...styles.crowdMember,
                      left: `${member.position}%`,
                      width: `${member.width}px`,
                      height: `${member.height}px`,
                      backgroundColor: member.team === 'home' ? homeColor : awayColor,
                      opacity: member.opacity,
                      animationDelay: `${member.waveOffset}s`,
                    }}
                  />
                ))}
              </div>
            );
          })}
        </div>

        {/* Upper arena LED board (trapezoidal) */}
        <div style={styles.ledBoard}>
          <svg style={styles.ledBoardSvg} viewBox="0 0 100 10" preserveAspectRatio="none">
            <defs>
              <linearGradient id="ledGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor={homeColor} />
                <stop offset="25%" stopColor={homeColor} />
                <stop offset="50%" stopColor="rgba(255,255,255,0.3)" />
                <stop offset="75%" stopColor={awayColor} />
                <stop offset="100%" stopColor={awayColor} />
              </linearGradient>
            </defs>
            <polygon
              points="35,0 65,0 75,10 25,10"
              fill="url(#ledGrad)"
              opacity="0.4"
            />
          </svg>
        </div>

        {/* Court edge/baseline with perspective */}
        <div style={styles.courtEdge}>
          <svg style={styles.courtEdgeSvg} viewBox="0 0 100 30" preserveAspectRatio="none">
            {/* Court floor lines converging */}
            <line x1="20" y1="0" x2="0" y2="30" stroke="rgba(139,69,19,0.3)" strokeWidth="0.5" />
            <line x1="40" y1="0" x2="25" y2="30" stroke="rgba(139,69,19,0.3)" strokeWidth="0.5" />
            <line x1="60" y1="0" x2="75" y2="30" stroke="rgba(139,69,19,0.3)" strokeWidth="0.5" />
            <line x1="80" y1="0" x2="100" y2="30" stroke="rgba(139,69,19,0.3)" strokeWidth="0.5" />
            
            {/* Baseline */}
            <line x1="0" y1="30" x2="100" y2="30" stroke="rgba(255,255,255,0.15)" strokeWidth="1.5" />
          </svg>
        </div>

        {/* Sideline banners with perspective */}
        <div style={styles.sideBanners}>
          {/* Left side - home team */}
          <div style={{...styles.sideBanner, left: '5%', background: `linear-gradient(135deg, ${homeColor}40 0%, ${homeColor}10 100%)`}} />
          {/* Right side - away team */}
          <div style={{...styles.sideBanner, right: '5%', background: `linear-gradient(225deg, ${awayColor}40 0%, ${awayColor}10 100%)`}} />
        </div>

        {/* Floating particles in light beams */}
        <div style={styles.particles}>
          {[...Array(25)].map((_, i) => {
            const x = Math.random() * 100;
            const y = Math.random() * 70;
            const size = 1 + Math.random() * 2;
            
            return (
              <div
                key={i}
                className="arena-particle"
                style={{
                  ...styles.particle,
                  left: `${x}%`,
                  top: `${y}%`,
                  width: `${size}px`,
                  height: `${size}px`,
                  animationDelay: `${Math.random() * 5}s`,
                  animationDuration: `${4 + Math.random() * 4}s`,
                }}
              />
            );
          })}
        </div>

        {/* Vignette for depth */}
        <div style={styles.vignette} />
        
        {/* Team atmosphere gradient */}
        <div 
          style={{
            ...styles.teamAtmosphere,
            background: `linear-gradient(90deg, 
              ${homeColor}25 0%, 
              transparent 25%, 
              transparent 75%, 
              ${awayColor}25 100%)`,
          }}
        />
      </div>
    </div>
  );
}

const styles = {
  container: {
    position: 'absolute',
    inset: 0,
    overflow: 'hidden',
    background: 'linear-gradient(180deg, #0a0a12 0%, #0d1117 40%, #111827 70%, #1a1f2e 100%)',
    zIndex: 0,
  },
  perspectiveScene: {
    position: 'absolute',
    inset: 0,
    perspective: '800px',
    perspectiveOrigin: '50% 50%',
  },
  ceiling: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: '35%',
    overflow: 'hidden',
  },
  ceilingStructure: {
    position: 'absolute',
    top: 0,
    left: 0,
    width: '100%',
    height: '100%',
  },
  ceilingLight: {
    position: 'absolute',
    inset: 0,
    filter: 'blur(40px)',
  },
  spotlightContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: '80%',
    pointerEvents: 'none',
  },
  spotlightBeam: {
    position: 'absolute',
    inset: 0,
    filter: 'blur(25px)',
  },
  crowdContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    height: '70%',
    pointerEvents: 'none',
  },
  crowdRow: {
    position: 'absolute',
    left: 0,
    right: 0,
    overflow: 'hidden',
  },
  crowdBackdrop: {
    position: 'absolute',
    inset: 0,
    filter: 'blur(8px)',
  },
  crowdMember: {
    position: 'absolute',
    bottom: 0,
    borderRadius: '2px 2px 0 0',
    animation: 'crowdBob 2s ease-in-out infinite',
    transformOrigin: 'bottom center',
  },
  ledBoard: {
    position: 'absolute',
    top: '30%',
    left: 0,
    right: 0,
    height: '8%',
    pointerEvents: 'none',
  },
  ledBoardSvg: {
    width: '100%',
    height: '100%',
    filter: 'blur(2px)',
  },
  courtEdge: {
    position: 'absolute',
    bottom: '15%',
    left: 0,
    right: 0,
    height: '20%',
    pointerEvents: 'none',
  },
  courtEdgeSvg: {
    width: '100%',
    height: '100%',
    opacity: 0.4,
  },
  sideBanners: {
    position: 'absolute',
    top: '25%',
    left: 0,
    right: 0,
    height: '50%',
    pointerEvents: 'none',
  },
  sideBanner: {
    position: 'absolute',
    top: 0,
    width: '15%',
    height: '100%',
    clipPath: 'polygon(0 20%, 100% 0, 100% 100%, 0 80%)',
    opacity: 0.3,
    filter: 'blur(4px)',
  },
  particles: {
    position: 'absolute',
    inset: 0,
    pointerEvents: 'none',
    overflow: 'hidden',
  },
  particle: {
    position: 'absolute',
    background: 'rgba(255, 255, 255, 0.4)',
    borderRadius: '50%',
    animation: 'particleFloat 6s ease-in-out infinite',
    filter: 'blur(1px)',
  },
  vignette: {
    position: 'absolute',
    inset: 0,
    background: `
      radial-gradient(ellipse 70% 60% at 50% 40%, transparent 30%, rgba(0,0,0,0.4) 70%, rgba(0,0,0,0.8) 100%),
      linear-gradient(180deg, transparent 60%, rgba(0,0,0,0.3) 100%)
    `,
    pointerEvents: 'none',
  },
  teamAtmosphere: {
    position: 'absolute',
    inset: 0,
    pointerEvents: 'none',
    filter: 'blur(60px)',
  },
};


