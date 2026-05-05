import * as React from "react";

type Props = {
  value: number;
  min?: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  className?: string;
};

export function Slider({ value, min = 0, max, step = 1, onChange, className = "" }: Props) {
  return (
    <input
      type="range"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(e) => onChange(Number(e.target.value))}
      className={`w-full accent-accent ${className}`}
    />
  );
}
