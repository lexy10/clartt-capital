import {
  Entity,
  PrimaryColumn,
  Column,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { User } from '../../auth/entities/user.entity';

@Entity('kill_switch')
export class KillSwitch {
  @PrimaryColumn({ type: 'integer', default: 1 })
  id: number;

  @Column({ type: 'boolean', name: 'is_active', default: false })
  isActive: boolean;

  @Column({ type: 'uuid', name: 'activated_by', nullable: true })
  activatedBy: string | null;

  @ManyToOne(() => User, { nullable: true })
  @JoinColumn({ name: 'activated_by' })
  activator: User;

  @Column({ type: 'timestamptz', name: 'activated_at', nullable: true })
  activatedAt: Date | null;

  @Column({ type: 'timestamptz', name: 'deactivated_at', nullable: true })
  deactivatedAt: Date | null;
}
