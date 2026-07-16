`timescale 1ns / 1ps
module tb_traffic_light();
reg clk;
reg rst_n;
wire red, yellow, green;
// Instantiate the Unit Under Test (UUT)
traffic_light_ctrl uut (
.clk(clk),
.rst_n(rst_n),
.red(red),
.yellow(yellow),
.green(green)
);
// Clock generation: 10ns period
initial clk = 0;
always #5 clk = ~clk;
integer i;
initial begin
// Initialize Inputs
rst_n = 0;
#12 rst_n = 1; // Deassert reset after 12ns (ensures async reset triggers)
// After reset deasserts, sequence should be:
// Green (3 cyc) -> Yellow (1 cyc) -> Red (4 cyc) -> Green...
for (i = 0; i < 8; i = i + 1) begin
@(posedge clk);
#1; // Delay to check stable output after clock edge
if (i < 3) begin
if (green !== 1'b1 || red !== 1'b0 || yellow !== 1'b0) begin
$display("ERROR: At cycle %0d, expected GREEN. Got R:%b Y:%b G:%b", i, red, yellow, green);
$finish;
end
end else if (i == 3) begin
if (yellow !== 1'b1 || red !== 1'b0 || green !== 1'b0) begin
$display("ERROR: At cycle %0d, expected YELLOW. Got R:%b Y:%b G:%b", i, red, yellow, green);
$finish;
end
end else begin // i = 4, 5, 6, 7
if (red !== 1'b1 || green !== 1'b0 || yellow !== 1'b0) begin
$display("ERROR: At cycle %0d, expected RED. Got R:%b Y:%b G:%b", i, red, yellow, green);
$finish;
end
end
end
$display("SUCCESS: Traffic light sequence verified correctly.");
$finish;
end
endmodule

