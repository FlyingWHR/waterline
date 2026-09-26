// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {Test} from "forge-std/Test.sol";
import {Marks} from "../src/Marks.sol";
import {EnsNames} from "./EnsNames.sol";
import {IEnhancedAccessControl} from "@ensdomains/contracts-v2/access-control/interfaces/IEnhancedAccessControl.sol";

contract MarksTest is Test {
    Marks marks;
    address admin = address(0xA11CE);
    address api = address(0xA91);
    address host = address(0xBAD);
    bytes32 parent = EnsNames.namehash("waterline.eth");
    bytes32 cloudB = keccak256("cloud-b");
    bytes32 cloudA = keccak256("cloud-a");
    bytes32 gpu1 = keccak256("gpu-91c0ab12");
    bytes32 gpu2 = keccak256("gpu-2b44aa01");
    bytes32 node = EnsNames.namehash("gpu-91c0ab12.cloud-b.waterline.eth");
    bytes32 node2 = EnsNames.namehash("gpu-2b44aa01.cloud-b.waterline.eth");
    bytes32 provider = EnsNames.namehash("cloud-b.waterline.eth");
    bytes32 fp = bytes32(uint256(0x5e2b));

    function setUp() public {
        marks = new Marks(admin, api, parent);
    }

    function pass(bytes32 gpu) internal {
        vm.prank(api);
        marks.record(cloudB, gpu, 1, 1, 132, fp, 0, 0, 14105, 7125, bytes32(0));
    }

    /// one World ID proof -> a per-GPU voter and a per-provider voter for the same human
    function fail(bytes32 gpu, string memory human) internal {
        vm.prank(api);
        marks.record(cloudB, gpu, 2, 3, 108, fp, keccak256(abi.encode(human, gpu)), keccak256(abi.encode(human, "cloud-b")), 0, 0, 0);
    }

    function test_nodesFollowTheEnsTree() public view {
        (bytes32 p, bytes32 n) = marks.nodes(cloudB, gpu1);
        assertEq(p, provider);
        assertEq(n, node);
    }

    function test_passIsRecorded() public {
        pass(gpu1);
        assertEq(marks.text(node, "waterline.status"), "pass");
        assertEq(marks.text(node, "waterline.tops"), "1410.5");
        assertEq(marks.text(node, "waterline.pct_of_spec"), "71.25");
        assertEq(marks.text(node, "waterline.class"), "H100 SXM");
        assertEq(marks.text(node, "waterline.cores"), "132");
        assertEq(marks.text(node, "waterline.passes"), "1");
    }

    function test_hostCannotPostItsOwnPass() public {
        uint256 role = marks.ROLE_REPORTER();
        vm.prank(host);
        vm.expectRevert(abi.encodeWithSelector(
            IEnhancedAccessControl.EACUnauthorizedAccountRoles.selector, uint256(node), role, host));
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
    }

    function test_oneFailureIsSuspectTwoHumansFail() public {
        fail(gpu1, "human-1");
        assertEq(marks.text(node, "waterline.status"), unicode"suspect · 1 of 2 humans");
        fail(gpu1, "human-2");
        assertEq(marks.text(node, "waterline.status"), "failed");
        assertEq(marks.text(node, "waterline.class"), "A100");
        assertEq(marks.text(node, "waterline.humans"), "2");
    }

    function test_sameHumanCannotVoteTwiceOnOneGpu() public {
        fail(gpu1, "human-1");
        vm.expectRevert(Marks.AlreadyVoted.selector);
        fail(gpu1, "human-1");
    }

    function test_failureNeedsBothVoters() public {
        vm.startPrank(api);
        vm.expectRevert(Marks.AlreadyVoted.selector);
        marks.record(cloudB, gpu1, 2, 3, 108, fp, 0, keccak256("p"), 0, 0, 0);
        vm.expectRevert(Marks.AlreadyVoted.selector);
        marks.record(cloudB, gpu1, 2, 3, 108, fp, keccak256("g"), 0, 0, 0, 0);
        vm.stopPrank();
    }

    function test_badVerdictReverts() public {
        vm.prank(api);
        vm.expectRevert(Marks.BadVerdict.selector);
        marks.record(cloudB, gpu1, 7, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
    }

    // ---- provider roll-up --------------------------------------------------------------------

    function test_failuresRollUpToTheProvider() public {
        pass(gpu2);
        fail(gpu1, "human-1");
        fail(gpu1, "human-2");
        assertEq(marks.text(provider, "waterline.gpus"), "2");
        assertEq(marks.text(provider, "waterline.failed_gpus"), "1");
        assertEq(marks.text(provider, "waterline.humans"), "2");
        assertEq(marks.text(provider, "waterline.passes"), "1");
        assertEq(marks.text(provider, "waterline.fails"), "2");
        assertEq(marks.text(provider, "waterline.status"), unicode"1 of 2 GPUs failed · reported by 2 people");
    }

    function test_oneHumanCountsOncePerProvider() public {
        fail(gpu1, "human-1");
        fail(gpu2, "human-1"); // a heavy renter flags a second bad pod: allowed per GPU...
        assertEq(marks.text(node2, "waterline.status"), unicode"suspect · 1 of 2 humans");
        assertEq(marks.text(provider, "waterline.humans"), "1"); // ...but counts once for the provider
        assertEq(marks.text(provider, "waterline.fails"), "2");
    }

    function test_renamingAChipDoesNotCleanTheProvider() public {
        fail(gpu1, "human-1");
        fail(gpu1, "human-2");
        pass(gpu2); // same card under a new UUID: a clean GPU name...
        assertEq(marks.text(node2, "waterline.status"), "pass");
        assertEq(marks.text(provider, "waterline.failed_gpus"), "1"); // ...not a clean provider
    }

    function test_providersAreSeparate() public {
        fail(gpu1, "human-1");
        (bytes32 otherProvider,) = marks.nodes(cloudA, gpu1);
        assertEq(marks.text(otherProvider, "waterline.status"), "unknown");
        assertEq(marks.text(provider, "waterline.gpus"), "1");
        assertEq(marks.text(EnsNames.namehash("gpu-91c0ab12.cloud-a.waterline.eth"), "waterline.status"), "unknown");
    }

    // ---- recovery ------------------------------------------------------------------------------

    function test_twoPassesAfterTheLastFailureRecover() public {
        fail(gpu1, "human-1");
        fail(gpu1, "human-2");
        pass(gpu1);
        assertEq(marks.text(node, "waterline.status"), "failed");
        assertEq(marks.text(provider, "waterline.failed_gpus"), "1");
        pass(gpu1);
        assertEq(marks.text(node, "waterline.status"), "recovered");
        assertEq(marks.text(node, "waterline.recoveries"), "1");
        assertEq(marks.text(node, "waterline.humans"), "2"); // history stays
        assertEq(marks.text(provider, "waterline.failed_gpus"), "0");
    }

    function test_recoveredGpuNeedsNewHumansToFailAgain() public {
        fail(gpu1, "human-1");
        pass(gpu1);
        pass(gpu1);
        assertEq(marks.text(node, "waterline.status"), "recovered");
        vm.expectRevert(Marks.AlreadyVoted.selector);
        fail(gpu1, "human-1");
        fail(gpu1, "human-3");
        assertEq(marks.text(node, "waterline.status"), unicode"suspect · 1 of 2 humans");
    }

    function test_aFailureResetsThePassCount() public {
        fail(gpu1, "human-1");
        pass(gpu1);
        fail(gpu1, "human-2");
        pass(gpu1);
        assertEq(marks.text(node, "waterline.status"), "failed");
    }

    // ---- degraded: right chip, too slow (heat-proof class, heat-sensitive throughput) ----------------

    function degraded(bytes32 gpu) internal {
        vm.prank(api);
        marks.record(cloudB, gpu, 3, 1, 132, fp, 0, 0, 4200, 2100, bytes32(0));
    }

    function test_degradedNeedsNoHumanAndNeverFails() public {
        degraded(gpu1);
        degraded(gpu1);
        degraded(gpu1);
        assertEq(marks.text(node, "waterline.status"), "degraded");
        assertEq(marks.text(node, "waterline.degraded"), "3");
        assertEq(marks.text(node, "waterline.class"), "H100 SXM");
        assertEq(marks.text(node, "waterline.pct_of_spec"), "21.00");
        assertEq(marks.text(provider, "waterline.failed_gpus"), "0");
        assertEq(marks.text(provider, "waterline.degraded"), "3");
        pass(gpu1); // the latest check speaks: back at speed
        assertEq(marks.text(node, "waterline.status"), "pass");
    }

    function test_degradedIsNoRecoveryCredit() public {
        fail(gpu1, "human-1");
        pass(gpu1);
        degraded(gpu1);
        assertEq(marks.text(node, "waterline.status"), unicode"suspect · 1 of 2 humans");
        pass(gpu1); // two passes since the failure: the degraded check in between doesn't count, doesn't reset
        assertEq(marks.text(node, "waterline.status"), "recovered");
    }

    // ---- provider note (voice, never verdict) ----------------------------------------------

    function test_providerNoteNeedsTheNoteRoleOnItsOwnName() public {
        fail(gpu1, "human-1");
        uint256 role = marks.ROLE_NOTE();
        vm.prank(host);
        vm.expectRevert(abi.encodeWithSelector(
            IEnhancedAccessControl.EACUnauthorizedAccountRoles.selector, uint256(provider), role, host));
        marks.setNote(provider, "We replaced the card.");
        vm.prank(admin);
        marks.grantRoles(uint256(provider), role, host);
        vm.prank(host);
        marks.setNote(provider, "We replaced the card.");
        assertEq(marks.text(provider, "waterline.note"), "We replaced the card.");
        assertEq(marks.text(node, "waterline.status"), unicode"suspect · 1 of 2 humans"); // the note changes nothing
        (bytes32 otherProvider,) = marks.nodes(cloudA, gpu1);
        vm.prank(host);
        vm.expectRevert();
        marks.setNote(otherProvider, "not mine");
    }

    function test_noteRoleCannotReport() public {
        vm.startPrank(admin);
        marks.grantRoles(uint256(provider), marks.ROLE_NOTE(), host);
        vm.stopPrank();
        vm.prank(host);
        vm.expectRevert();
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
    }

    function test_noteLengthIsCapped() public {
        vm.startPrank(admin);
        marks.grantRoles(uint256(provider), marks.ROLE_NOTE(), host);
        vm.stopPrank();
        bytes memory long = new bytes(281);
        vm.prank(host);
        vm.expectRevert(Marks.NoteTooLong.selector);
        marks.setNote(provider, string(long));
    }

    // ---- ENS, events, roles --------------------------------------------------------------

    function test_resolveAnswersTextAndAddr() public {
        pass(gpu1);
        bytes memory out = marks.resolve("", abi.encodeWithSelector(marks.text.selector, node, "waterline.status"));
        assertEq(abi.decode(out, (string)), "pass");
        out = marks.resolve("", abi.encodeWithSelector(marks.text.selector, provider, "waterline.gpus"));
        assertEq(abi.decode(out, (string)), "1");
        out = marks.resolve("", abi.encodeWithSelector(bytes4(0x3b3b57de), node));
        assertEq(abi.decode(out, (address)), address(0));
    }

    function test_unknownGpuAndFingerprintFormat() public {
        assertEq(marks.text(node, "waterline.status"), "unknown");
        assertEq(marks.text(node, "waterline.fingerprint"), "");
        pass(gpu1);
        assertEq(bytes(marks.text(node, "waterline.fingerprint")).length, 66);
    }

    function test_supportsInterfaces() public view {
        assertTrue(marks.supportsInterface(0x9061b923));
        assertTrue(marks.supportsInterface(0x59d1d43c));
        assertTrue(marks.supportsInterface(0x01ffc9a7));
        assertTrue(marks.supportsInterface(type(IEnhancedAccessControl).interfaceId));
        assertFalse(marks.supportsInterface(0xffffffff));
    }

    function test_perfTextRecordsKeepZeroPaddingAndLatest() public {
        assertEq(marks.text(node, "waterline.tops"), "");
        vm.startPrank(api);
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 14105, 7125, bytes32(0));
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 7, 705, bytes32(0));
        vm.stopPrank();
        assertEq(marks.text(node, "waterline.tops"), "0.7");
        assertEq(marks.text(node, "waterline.pct_of_spec"), "7.05");
    }

    function test_eventsCarryTheReportAndTheProviderTally() public {
        bytes32 gv = keccak256(abi.encode("human-1", gpu1));
        bytes32 pv = keccak256(abi.encode("human-1", "cloud-b"));
        vm.expectEmit(true, true, false, true);
        emit Marks.Reported(node, provider, gv, pv, 2, 3, 108, fp, 0, 0, uint64(block.timestamp), 0, 1, 1, bytes32(0));
        vm.expectEmit(true, false, false, true);
        emit Marks.ProviderTally(provider, 1, 0, 1, 0, 1, uint64(block.timestamp));
        fail(gpu1, "human-1");
    }

    function test_onlyAdminGrantsTheReporterRole() public {
        uint256 role = marks.ROLE_REPORTER();
        vm.prank(host);
        vm.expectRevert();
        marks.grantRootRoles(role, host);
        vm.prank(admin);
        marks.grantRootRoles(role, address(0x2));
        assertTrue(marks.hasRootRoles(role, address(0x2)));
        vm.prank(admin);
        marks.revokeRootRoles(role, api);
        vm.prank(api);
        vm.expectRevert();
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
    }

    /// The next stage in one call: an independent verifier allowed to write one GPU's record, and no other.
    function test_verifierScopedToOneGpu() public {
        address verifier = address(0xBEEF);
        uint256 role = marks.ROLE_REPORTER();
        vm.prank(admin);
        marks.grantRoles(uint256(node), role, verifier);
        vm.startPrank(verifier);
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
        vm.expectRevert();
        marks.record(cloudB, gpu2, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
        vm.stopPrank();
        assertEq(marks.text(node, "waterline.passes"), "1");
    }

    /// A verifier for a whole provider covers every GPU under it, and nothing under another provider.
    function test_verifierScopedToOneProvider() public {
        address verifier = address(0xBEEF);
        uint256 role = marks.ROLE_REPORTER();
        vm.prank(admin);
        marks.grantRoles(uint256(provider), role, verifier);
        vm.startPrank(verifier);
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
        marks.record(cloudB, gpu2, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
        vm.expectRevert();
        marks.record(cloudA, gpu1, 1, 1, 132, fp, 0, 0, 0, 0, bytes32(0));
        vm.stopPrank();
    }

    function test_reportHashAnchorsTheEvidence() public {
        bytes32 h = keccak256("canonical report json");
        assertEq(marks.text(node, "waterline.report"), "");
        vm.prank(api);
        marks.record(cloudB, gpu1, 1, 1, 132, fp, 0, 0, 14105, 7125, h);
        assertEq(marks.text(node, "waterline.report"), vm.toString(h));
    }
}
