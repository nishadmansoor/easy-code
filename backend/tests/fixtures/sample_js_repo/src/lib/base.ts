import { Shape } from "../utils";

export abstract class Base implements Shape {
  abstract area(): number;
  describe(): string { return `area=${this.area()}`; }
}
