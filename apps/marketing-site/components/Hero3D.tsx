"use client";

import React, { useEffect, useMemo, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import {
  Float,
  RoundedBox,
  Text,
  PointMaterial,
  Torus,
  Sphere,
  MeshDistortMaterial
} from "@react-three/drei";
import * as THREE from "three";
import { EffectComposer, Bloom } from "@react-three/postprocessing";
import { useScroll } from "framer-motion";

function SvgCore() {
  const meshRef = useRef<THREE.Mesh>(null!);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const s = 1 + Math.sin(t * 1.3) * 0.025;
    meshRef.current.scale.setScalar(s);
  });
  return (
    <mesh ref={meshRef}>
      <circleGeometry args={[2.45, 96]} />
      <meshStandardMaterial color="#140422" metalness={0.55} roughness={0.34} />
    </mesh>
  );
}

function NebulaParticles({ count = 700 }: { count?: number }) {
  const pos = useMemo(() => {
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      arr[i * 3] = (Math.random() - 0.5) * 40;
      arr[i * 3 + 1] = (Math.random() - 0.5) * 22;
      arr[i * 3 + 2] = (Math.random() - 0.5) * 40;
    }
    return arr;
  }, [count]);

  const geometry = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    return geo;
  }, [pos]);

  useEffect(() => () => geometry.dispose(), [geometry]);

  const ref = useRef<THREE.Points>(null!);
  useFrame(({ clock }) => {
    ref.current.rotation.y = clock.elapsedTime * 0.02;
    ref.current.rotation.x = Math.sin(clock.elapsedTime * 0.12) * 0.05;
  });

  return (
    <points ref={ref as React.MutableRefObject<THREE.Points>} geometry={geometry} frustumCulled>
      <PointMaterial transparent size={0.036} depthWrite={false} color="#d6b9ff" />
    </points>
  );
}

function OrbitRing() {
  return (
    <group rotation={[-Math.PI / 2.3, 0, Math.PI / 6]}>
      <Torus args={[6.6, 0.1, 64, 220]}>
        <meshStandardMaterial
          color="#ff85c9"
          emissive="#ff8aca"
          emissiveIntensity={0.68}
          metalness={0.38}
          roughness={0.28}
        />
      </Torus>
    </group>
  );
}

function OrbitingBadge(props: { label: string; radius: number; speed: number; yOffset: number; color: string }) {
  const { label, radius, speed, yOffset, color } = props;
  const group = useRef<THREE.Group>(null!);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime * speed;
    const x = Math.cos(t) * radius;
    const z = Math.sin(t) * radius;
    group.current.position.set(x, yOffset + Math.sin(t * 1.4) * 0.45, z);
    group.current.rotation.y = -t + Math.PI / 2;
  });
  return (
    <group ref={group as React.MutableRefObject<THREE.Group>}>
      <Float speed={1.2} rotationIntensity={0.14} floatIntensity={0.45}>
        <RoundedBox args={[3.1, 1.55, 0.2]} radius={0.24} smoothness={10}>
          <meshStandardMaterial color="#0b031a" metalness={0.26} roughness={0.58} />
        </RoundedBox>
      </Float>
      <mesh position={[0, 0, -0.01]}>
        <planeGeometry args={[3.25, 1.7]} />
        <meshBasicMaterial color={color} transparent opacity={0.18} />
      </mesh>
      <Text position={[-1.3, 0.06, 0.12]} fontSize={0.24} color="#f8fbff" anchorX="left">
        {label}
      </Text>
      <mesh position={[1.25, -0.32, 0.12]}>
        <sphereGeometry args={[0.13, 24, 24]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.96} />
      </mesh>
    </group>
  );
}

function OrbitingSphere({ radius, speed, size, color }: { radius: number; speed: number; size: number; color: string }) {
  const ref = useRef<THREE.Mesh>(null!);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime * speed;
    const x = Math.cos(t) * radius;
    const z = Math.sin(t) * radius;
    ref.current.position.set(x, Math.sin(t * 1.5) * 0.8, z);
  });
  return (
    <Sphere ref={ref as React.MutableRefObject<THREE.Mesh>} args={[size, 32, 32]}>
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.9} metalness={0.34} roughness={0.25} />
    </Sphere>
  );
}

function PostFX() {
  return (
    <EffectComposer>
      <Bloom luminanceThreshold={0.24} luminanceSmoothing={0.78} intensity={1.35} />
    </EffectComposer>
  );
}

function CameraRig() {
  const { camera } = useThree();
  const { scrollYProgress } = useScroll();
  const targetZ = useRef(12);
  useFrame(() => {
    const s = scrollYProgress.get();
    targetZ.current = 12 - s * 3;
    camera.position.z += (targetZ.current - camera.position.z) * 0.05;
    camera.lookAt(0, 0, 0);
  });
  return null;
}

function OrbitGraph() {
  const ref = useRef<THREE.Group>(null!);
  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    ref.current.rotation.y = t * 0.26;
    ref.current.rotation.x = Math.sin(t * 0.18) * 0.22;
  });

  const nodePositions = useMemo(() => {
    const nodes: [number, number, number][] = [];
    const layers = [
      { radius: 2.4, count: 8 },
      { radius: 1.6, count: 6 },
      { radius: 0.9, count: 4 }
    ];
    layers.forEach(({ radius, count }) => {
      for (let i = 0; i < count; i++) {
        const phi = (Math.PI * 2 * i) / count;
        const theta = Math.acos(THREE.MathUtils.clamp(1 - (2 * i) / count, -1, 1));
        nodes.push([
          radius * Math.sin(theta) * Math.cos(phi),
          radius * Math.cos(theta),
          radius * Math.sin(theta) * Math.sin(phi)
        ]);
      }
    });
    return nodes;
  }, []);

  const edges = useMemo(() => {
    const pairs: [THREE.Vector3, THREE.Vector3][] = [];
    const vecNodes = nodePositions.map(([x, y, z]) => new THREE.Vector3(x, y, z));
    vecNodes.forEach((node, idx) => {
      vecNodes.forEach((other, jdx) => {
        if (idx === jdx) return;
        if (node.distanceTo(other) < 1.8) {
          pairs.push([node.clone(), other.clone()]);
        }
      });
    });
    return pairs;
  }, [nodePositions]);

  return (
    <group ref={ref}>
      {edges.map(([from, to], idx) => (
        <mesh key={`edge-${idx}`} position={from.clone().add(to).multiplyScalar(0.5)}>
          <cylinderGeometry args={[0.018, 0.018, from.distanceTo(to), 16]} />
          <meshBasicMaterial color="#c8a6ff" transparent opacity={0.26} />
        </mesh>
      ))}
      {nodePositions.map(([x, y, z], idx) => (
        <mesh key={`node-${idx}`} position={[x, y, z]}>
          <sphereGeometry args={[0.16, 24, 24]} />
          <meshStandardMaterial
            color={idx % 3 === 0 ? "#ff9ec9" : idx % 3 === 1 ? "#9c8bff" : "#ffd49a"}
            emissive={idx % 3 === 0 ? "#ff8aca" : idx % 3 === 1 ? "#a99dff" : "#ffe0af"}
            emissiveIntensity={0.96}
            metalness={0.32}
            roughness={0.27}
          />
        </mesh>
      ))}
      <mesh>
        <sphereGeometry args={[0.72, 64, 64]} />
        <MeshDistortMaterial color="#170834" distort={0.35} speed={2.7} radius={0.8} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.2, 32, 32]} />
        <meshBasicMaterial color="#ff9bb7" />
      </mesh>
    </group>
  );
}

const orbitBadges = [
  { label: "Intent Orchestration", radius: 6.4, speed: 0.24, yOffset: 1.25, color: "#ff8aca" },
  { label: "Perp & DEX Routing", radius: 7.2, speed: -0.18, yOffset: -0.4, color: "#9c8bff" },
  { label: "NFT Strategies", radius: 5.6, speed: 0.32, yOffset: 1.9, color: "#ffd8a8" },
  { label: "Yield Automation", radius: 6.1, speed: -0.28, yOffset: -1.5, color: "#ff7f9d" }
];

const orbitSpheres = [
  { radius: 4.1, speed: 0.6, size: 0.28, color: "#ff8aca" },
  { radius: 5.4, speed: -0.45, size: 0.34, color: "#9d87ff" },
  { radius: 6.6, speed: 0.35, size: 0.24, color: "#ffd8a8" }
];

export default function Hero3D({ className }: { className?: string }) {
  const wrapperClass = `relative w-full h-[360px] sm:h-[420px] md:h-[520px] lg:h-[600px] overflow-hidden rounded-[24px] sm:rounded-[30px] md:rounded-[36px] border border-white/10 bg-[#0a031d]/65 backdrop-blur-2xl shadow-[0_45px_140px_-58px_rgba(255,124,210,0.55)] ${className ?? ""}`;

  return (
    <div className={wrapperClass}>
      <Canvas camera={{ position: [0, 0, 8], fov: 45 }} dpr={[1, 1.5]}>
        <color attach="background" args={["#070123"]} />
        <ambientLight intensity={0.38} color="#ffdcb4" />
        <directionalLight position={[4, 6, 6]} intensity={1.1} color="#ff89ca" />
        <directionalLight position={[-6, -3, -4]} intensity={0.62} color="#9d87ff" />

        <NebulaParticles count={760} />

        <Float speed={0.38} rotationIntensity={0.22} floatIntensity={0.18}>
          <OrbitRing />
        </Float>

        <Float speed={1.05} rotationIntensity={0.42} floatIntensity={0.62}>
          <OrbitGraph />
        </Float>

        <Float speed={0.74} rotationIntensity={0.18} floatIntensity={0.28}>
          <SvgCore />
        </Float>

        <Float speed={0.52} rotationIntensity={0.2} floatIntensity={0.4}>
          {orbitBadges.map((badge) => (
            <OrbitingBadge key={badge.label} {...badge} />
          ))}
          {orbitSpheres.map((sphere) => (
            <OrbitingSphere key={`${sphere.radius}-${sphere.speed}`} {...sphere} />
          ))}
        </Float>

        <PostFX />
        <CameraRig />
      </Canvas>
    </div>
  );
}
