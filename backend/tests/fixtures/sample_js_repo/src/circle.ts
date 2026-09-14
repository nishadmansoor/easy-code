import { Base } from "./lib/base";
import { round2 } from "./utils";

export class Circle extends Base {
  constructor(private r: number) { super(); }
  area(): number { return round2(Math.PI * this.r ** 2); }
}

export function makeCircle(r: number): Circle {
  return new Circle(r);
}
