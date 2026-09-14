const { makeCircle } = require("./src/circle");
import React from "react";

export function main() {
  const c = makeCircle(2);
  return c.describe();
}
