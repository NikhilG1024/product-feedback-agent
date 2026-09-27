import { it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Modal } from "../src/ui";
import { GuidanceDialog } from "../src/GuidanceDialog";
import { DemoApi } from "../src/demo";
import { ApiError } from "../src/api";
it("prevents native Escape close when the caller is busy", () => {
  render(
    <Modal title="Busy" onClose={() => {}}>
      Working
    </Modal>,
  );
  const event = new Event("cancel", { bubbles: true, cancelable: true });
  fireEvent(screen.getByRole("dialog"), event);
  expect(event.defaultPrevented).toBe(true);
  expect(screen.getByRole("dialog")).toHaveAttribute("open");
});
it("reconciles a lost guidance response without sending another POST", async () => {
  const api = new DemoApi();
  const original = api.decision.bind(api);
  const post = vi.spyOn(api, "decision").mockImplementation(async (p, b) => {
    await original(p, b);
    throw new ApiError(0, "network");
  });
  const user = userEvent.setup();
  render(
    <GuidanceDialog
      api={api}
      product={{ id: "demo-headphones", title: "Forma", product_type: null }}
      evidence={[]}
      onClose={() => {}}
    />,
  );
  await user.type(
    screen.getByLabelText("What should the team know?"),
    "Keep wear separate from runtime.",
  );
  await user.click(screen.getByRole("button", { name: "Save guidance" }));
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Save guidance" })).toBeDisabled();
  await user.click(
    screen.getByRole("button", { name: "Check whether it was saved" }),
  );
  expect(
    await screen.findByRole("heading", { name: "Your guidance is saved." }),
  ).toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
});
