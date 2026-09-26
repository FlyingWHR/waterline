// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {Test} from "forge-std/Test.sol";
import {Marks} from "../src/Marks.sol";

contract MarksTest is Test {
    Marks marks;
    address admin = address(0xA11CE);
    address api = address(0xA91);
    address host = address(0xBAD);
    bytes32 node = keccak256("gpu-91c0ab12.cloud-b.waterline.eth");
    bytes32 fp = bytes32(uint256(0x5e2b));

    function setUp() public {
        marks = new Marks(admin, api);
    }

    function test_passIsRecorded() public {
        vm.prank(api);
        marks.record(node, 1, 1, 132, fp, keccak256("report-1"), 14105, 7125);
        assertEq(marks.text(node, "waterline.status"), "pass");
        assertEq(marks.text(node, "waterline.tops"), "1410.5");
        assertEq(marks.text(node, "waterline.pct_of_spec"), "71.25");
        assertEq(marks.text(node, "waterline.class"), "H100 SXM");
        assertEq(marks.text(node, "waterline.cores"), "132");
        assertEq(marks.text(node, "waterline.passes"), "1");
    }

    function test_hostCannotPostItsOwnPass() public {
        vm.prank(host);
        vm.expectRevert(Marks.NotReporter.selector);
        marks.record(node, 1, 1, 132, fp, keccak256("fake"), 0, 0);
    }

    function test_oneFailureIsSuspectTwoHumansFail() public {
        vm.startPrank(api);
        marks.record(node, 2, 3, 108, fp, keccak256("human-1"), 0, 0);
        assertEq(marks.text(node, "waterline.status"), unicode"suspect · 1 of 2 humans");
        marks.record(node, 2, 3, 108, fp, keccak256("human-2"), 0, 0);
        vm.stopPrank();
        assertEq(marks.text(node, "waterline.status"), "failed");
        assertEq(marks.text(node, "waterline.class"), "A100");
        assertEq(marks.text(node, "waterline.humans"), "2");
    }

    function test_sameHumanCannotVoteTwiceOnOneGpu() public {
        vm.startPrank(api);
        marks.record(node, 2, 3, 108, fp, keccak256("human-1"), 0, 0);
        vm.expectRevert(Marks.AlreadyVoted.selector);
        marks.record(node, 2, 3, 108, fp, keccak256("human-1"), 0, 0);
        vm.stopPrank();
    }

    function test_failureNeedsAVoter() public {
        vm.prank(api);
        vm.expectRevert(Marks.AlreadyVoted.selector);
        marks.record(node, 2, 3, 108, fp, bytes32(0), 0, 0);
    }

    function test_badVerdictReverts() public {
        vm.prank(api);
        vm.expectRevert(Marks.BadVerdict.selector);
        marks.record(node, 7, 1, 132, fp, keccak256("x"), 0, 0);
    }

    function test_resolveAnswersTextAndAddr() public {
        vm.prank(api);
        marks.record(node, 1, 1, 132, fp, keccak256("r"), 0, 0);
        bytes memory out = marks.resolve("", abi.encodeWithSelector(marks.text.selector, node, "waterline.status"));
        assertEq(abi.decode(out, (string)), "pass");
        out = marks.resolve("", abi.encodeWithSelector(bytes4(0x3b3b57de), node));
        assertEq(abi.decode(out, (address)), address(0));
    }

    function test_unknownGpuAndFingerprintFormat() public {
        assertEq(marks.text(node, "waterline.status"), "unknown");
        assertEq(marks.text(node, "waterline.fingerprint"), "");
        vm.prank(api);
        marks.record(node, 1, 1, 132, fp, keccak256("r"), 0, 0);
        assertEq(bytes(marks.text(node, "waterline.fingerprint")).length, 66);
    }

    function test_supportsInterfaces() public view {
        assertTrue(marks.supportsInterface(0x9061b923));
        assertTrue(marks.supportsInterface(0x59d1d43c));
        assertTrue(marks.supportsInterface(0x01ffc9a7));
        assertFalse(marks.supportsInterface(0xffffffff));
    }

    function test_perfTextRecordsKeepZeroPaddingAndLatest() public {
        assertEq(marks.text(node, "waterline.tops"), "");
        vm.startPrank(api);
        marks.record(node, 1, 1, 132, fp, keccak256("r1"), 14105, 7125);
        marks.record(node, 1, 1, 132, fp, keccak256("r2"), 7, 705);
        vm.stopPrank();
        assertEq(marks.text(node, "waterline.tops"), "0.7");
        assertEq(marks.text(node, "waterline.pct_of_spec"), "7.05");
        (,,,,,,, uint32 tops, uint16 pct) = marks.gpus(node);
        assertEq(tops, 7);
        assertEq(pct, 705);
    }

    function test_reportedEventCarriesPerf() public {
        vm.expectEmit(true, false, false, true);
        emit Marks.Reported(node, keccak256("r"), 1, 1, 132, fp, 14105, 7125, uint64(block.timestamp), 1, 0, 0);
        vm.prank(api);
        marks.record(node, 1, 1, 132, fp, keccak256("r"), 14105, 7125);
    }

    function test_onlyAdminChangesReporter() public {
        vm.expectRevert(Marks.NotAdmin.selector);
        marks.setReporter(host);
        vm.prank(admin);
        marks.setReporter(address(0x2));
        assertEq(marks.reporter(), address(0x2));
    }
}
